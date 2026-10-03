from datetime import date
from decimal import Decimal
import json

import pytest

from bank_parser.extraction import extract_with_template
from bank_parser.extraction.parsing import ParseError, parse_amount
from bank_parser.extraction.unknown import extract_unknown
from bank_parser.pdf import open_pdf
from bank_parser.schema import Direction, statement_from_dict, statement_to_dict
from bank_parser.templates import TemplateRegistry
from bank_parser.validation import Status, validate_statement
from tests.conftest import SBI_EXPECTED
from tests.helpers import BACKENDS, COMMITTED_TEMPLATES, HDFC_LAYOUT, HDFC_PDF, SBI_LAYOUT, SBI_PDF, real_statement


@pytest.fixture(scope="module")
def registry():
    return TemplateRegistry([COMMITTED_TEMPLATES])


def _latest(registry, layout):
    bank, layout_id = layout.split("/")
    return registry.get(f"{layout}/v{max(registry.versions(bank, layout_id))}")


@pytest.mark.parametrize("text, value, suffix", [
    ("1,20,749.89", Decimal("120749.89"), None),
    ("9,999.99CR", Decimal("9999.99"), "CR"),
    ("12.00 Dr", Decimal("12.00"), "DR"),
    ("(5.00)", Decimal("-5.00"), None),
    ("-", None, None),
])
def test_parse_amount(text, value, suffix):
    assert parse_amount(text) == (value, suffix)


@pytest.mark.parametrize("bad", ["abc", "12,34x", "1.2.3", "NaN"])
def test_parse_amount_rejects(bad):
    with pytest.raises(ParseError):
        parse_amount(bad)


@pytest.mark.parametrize("backend", BACKENDS)
def test_sbi_template_extraction_verified(registry, backend):
    with open_pdf(SBI_PDF, backend=backend) as doc:
        result = extract_with_template(doc, _latest(registry, SBI_LAYOUT))
    s = result.statement
    assert result.problems == ()
    assert len(s.transactions) == 32
    assert validate_statement(s).status is Status.PASS
    assert s.header.ifsc == "SBIN0003606" and s.header.bank_name == "State Bank of India"
    assert s.header.period_start == date(2018, 4, 16) and s.header.period_end == date(2018, 10, 16)
    assert s.header.account_holder_name and s.header.account_holder_address
    assert s.provenance.template_id.startswith(SBI_LAYOUT)


@pytest.mark.parametrize("backend", BACKENDS)
def test_row_split_across_pages_is_kept(registry, backend):
    # pdfplumber's ruled-table finder drops this row (DECISIONS.md D-002); the word-column engine must not.
    with open_pdf(SBI_PDF, backend=backend) as doc:
        t = extract_with_template(doc, _latest(registry, SBI_LAYOUT)).statement.transactions[14]
    assert t.txn_date == date(2018, 7, 30) and t.reference == "367952"
    assert t.debit == Decimal("10000.00") and t.direction is Direction.DEBIT and t.amount == Decimal("10000.00")
    assert t.description.endswith("30902590252-367952")   # continuation from page 2
    assert t.page == 1


@pytest.mark.skipif(not SBI_EXPECTED.exists(), reason="local-only fixture missing")
@pytest.mark.parametrize("backend", BACKENDS)
def test_sbi_matches_hand_verified_ground_truth(registry, backend):
    expected = statement_from_dict(json.loads(SBI_EXPECTED.read_text(encoding="utf-8")))
    with open_pdf(SBI_PDF, backend=backend) as doc:
        got = extract_with_template(doc, _latest(registry, SBI_LAYOUT)).statement
    fields = ("row_index", "page", "txn_date", "value_date", "description", "reference", "debit", "credit", "balance")
    assert [[getattr(t, f) for f in fields] for t in got.transactions] == \
           [[getattr(t, f) for f in fields] for t in expected.transactions]


@pytest.mark.parametrize("backend", BACKENDS)
def test_hdfc_template_extraction(registry, backend):
    with open_pdf(HDFC_PDF, backend=backend) as doc:
        result = extract_with_template(doc, _latest(registry, HDFC_LAYOUT))
    s = result.statement
    report = validate_statement(s)
    chain = next(c for c in report.checks if c.check == "balance_chain")
    assert result.problems == ()
    assert len(s.transactions) == 20
    # No opening balance is printed: every row but the first is verified.
    assert chain.status is Status.WARN and [i.code for i in chain.issues] == ["no_opening_balance"]
    assert chain.rows_checked == 19
    assert s.header.account_holder_address and s.header.bank_address   # v2 region rules
    assert s.header.ifsc == "HDFC0001116"
    assert any("\n" not in t.description and " " in t.description for t in s.transactions)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("path, rows", [(SBI_PDF, 32), (HDFC_PDF, 20)])
def test_unknown_layout_extraction_without_templates(backend, path, rows):
    """Workflow 1 for an unknown layout: inferred, extracted and validated with no template at all."""
    with open_pdf(path, backend=backend) as doc:
        result, report = extract_unknown(doc, path, None)
    assert len(result.statement.transactions) == rows
    assert report.status is not Status.FAIL
    assert result.statement.provenance.template_id is None


def test_statement_round_trip(registry):
    with open_pdf(SBI_PDF) as doc:
        s = extract_with_template(doc, _latest(registry, SBI_LAYOUT)).statement
    data = statement_to_dict(s)
    assert data["transactions"][0]["direction"] == "DEBIT" and data["transactions"][0]["amount"] == "88.50"
    assert isinstance(data["header"]["opening_balance"], str)
    again = statement_from_dict(json.loads(json.dumps(data)))
    assert again.transactions == s.transactions and again.header == s.header


@pytest.mark.parametrize("backend", BACKENDS)
def test_real_statement_unknown_layout(backend):
    path, password = real_statement()
    if path is None:
        pytest.skip("local-only real statement not present")
    with open_pdf(path, password, backend) as doc:
        result, report = extract_unknown(doc, path, password)
    assert result.problems == ()
    assert report.status is Status.PASS and report.verified
    assert {c.check: c.status for c in report.checks}["reconciliation"] is Status.PASS
