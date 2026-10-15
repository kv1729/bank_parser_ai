"""US statement formats and validators (Stage 10.6, D-026). Synthetic layouts, fake data only."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from bank_parser.core.schema import statement_from_dict, statement_to_dict
from bank_parser.core.text_checks import text_check
from bank_parser.core.validation import routing_number_valid, validate_statement
from bank_parser.extraction.engine import resolve_years
from bank_parser.extraction.inference import choose_date_format
from bank_parser.extraction.parsing import CANDIDATE_DATE_FORMATS, looks_like_date_start, parse_amount, parse_date
from bank_parser.extraction.unknown import extract_unruled
from bank_parser.pdf.json_layout import load_layout
from bank_parser.self_review import build_self_review
from bank_parser.core.validation import report_to_dict

US = Path(__file__).parent / "fixtures" / "us_layouts"
ROW_KEYS = ("txn_date", "description", "debit", "credit", "balance")


def _read(name):
    _, doc = load_layout(US / name / "layout.json")
    truth = json.loads((US / name / "truth.json").read_text(encoding="utf-8"))
    result, report = extract_unruled(doc)
    assert result is not None, "Workflow 1 did not read the table"
    return statement_to_dict(result.statement), report, result, truth


@pytest.mark.parametrize("name", ["us-columns", "us-signed"])
def test_us_statement_is_read_exactly_as_printed(name):
    st, report, result, truth = _read(name)
    assert [{k: t[k] for k in ROW_KEYS} for t in st["transactions"]] == truth["transactions"]
    h = st["header"]
    for k in ("period_start", "period_end", "opening_balance", "closing_balance", "currency"):
        assert str(h[k]) == truth[k], k
    assert h["extra"]["routing_number"] == truth["routing_number"]
    assert st["summary"]["total_debits"] == truth["total_debits"]
    assert st["summary"]["total_credits"] == truth["total_credits"]
    checks = {c.check: c.status.value for c in report.checks}
    assert checks["balance_chain"] == "PASS" and checks["reconciliation"] == "PASS"
    assert not [c for c in report.checks if c.status.value == "FAIL"]
    assert result.problems == ()
    assert text_check(result.statement, result.text_trace).issues == ()


def test_year_less_rows_take_the_year_from_the_period_across_new_year():
    st, *_ = _read("us-columns")
    years = [t["txn_date"][:7] for t in st["transactions"]]
    assert years[0] == "2023-12" and years[-1] == "2024-01"


def test_year_less_rows_without_a_period_are_reported():
    st, _, result, _ = _read("us-columns")
    st["header"]["period_start"] = st["header"]["period_end"] = None
    for t in st["transactions"]:
        t["txn_date"] = "1904-" + t["txn_date"][5:]
    statement, problems = resolve_years(statement_from_dict(st))
    assert [p.code for p in problems] == ["date_year_unknown"]


def test_routing_number_checksum():
    assert routing_number_valid("123456780")
    assert not routing_number_valid("123456789")
    assert not routing_number_valid("12345678")
    st, *_ = _read("us-signed")
    st["header"]["extra"]["routing_number"] = "123456789"
    report = validate_statement(statement_from_dict(st))
    header = next(c for c in report.checks if c.check == "header")
    assert "invalid_routing_number" in [i.code for i in header.issues] and report.status.value == "FAIL"


def test_self_review_rates_a_valid_routing_number_high():
    st, report, result, _ = _read("us-signed")
    sr = build_self_review(st, report_to_dict(report), result.field_sources)
    assert sr["fields"]["header.extra.routing_number"]["level"] == "high"


def test_us_dates_and_amounts():
    f = CANDIDATE_DATE_FORMATS
    assert parse_date("12/15/2023", f) == date(2023, 12, 15)
    assert parse_date("Jan 5, 2024", f) == date(2024, 1, 5)
    assert parse_date("01/05", f).month == 1 and parse_date("01/05", f).day == 5
    assert looks_like_date_start("Jan 05, 2024") and not looks_like_date_start("Janet")
    assert parse_amount("45.10-") == (Decimal("-45.10"), None)
    assert parse_amount("-") == (None, None)


def test_indian_dates_keep_reading_day_first():
    f = CANDIDATE_DATE_FORMATS
    assert parse_date("01/05/2024", f) == date(2024, 5, 1)          # ambiguous: day-first, as before
    # A newest-first day-first statement whose days are all <= 12 keeps its day-first format.
    assert choose_date_format(["05/03/2024", "03/03/2024", "01/03/2024"]) == "%d/%m/%Y"
    assert choose_date_format(["01/03/2024", "03/03/2024", "05/03/2024"]) == "%d/%m/%Y"
    # Only a day above 12 in the second place makes it month-first.
    assert choose_date_format(["01/13/2024", "01/20/2024"]) == "%m/%d/%Y"
