from datetime import date
from decimal import Decimal

import pytest

from bank_parser import SchemaError, statement_from_dict


def test_sbi_ground_truth_loads(sbi_statement):
    s = sbi_statement
    assert s.header.ifsc == "SBIN0003606"
    assert s.header.opening_balance == Decimal("75222.71")
    assert len(s.transactions) == 32
    assert [t.row_index for t in s.transactions] == list(range(32))


def test_money_is_decimal_never_float(sbi_statement):
    for t in sbi_statement.transactions:
        for v in (t.debit, t.credit, t.balance):
            assert v is None or isinstance(v, Decimal)


def test_page_split_row_is_present(sbi_statement):
    # This row starts on page 1 and continues on page 2; pdfplumber's table
    # extraction drops it (DECISIONS.md D-002). The ground truth must keep it.
    row = sbi_statement.transactions[14]
    assert row.txn_date == date(2018, 7, 30)
    assert row.reference == "367952"
    assert row.debit == Decimal("10000.00")
    assert row.balance == Decimal("55246.99")


@pytest.mark.parametrize("bad", [
    120749.89,          # JSON number -> would have passed through float
    "1,20,749.89",      # display formatting, not normalized
    "12.345",           # more than 2 decimal places
    "abc",
    "NaN",
    "",
    True,
])
def test_malformed_money_rejected(sbi_raw, bad):
    sbi_raw["transactions"][3]["balance"] = bad
    with pytest.raises(SchemaError, match=r"transactions\[3\]\.balance"):
        statement_from_dict(sbi_raw)


@pytest.mark.parametrize("field, bad", [
    ("txn_date", "2018-02-30"),
    ("txn_date", None),
    ("row_index", "3"),
    ("description", None),
])
def test_malformed_fields_rejected(sbi_raw, field, bad):
    sbi_raw["transactions"][0][field] = bad
    with pytest.raises(SchemaError):
        statement_from_dict(sbi_raw)


def test_unknown_schema_version_rejected(sbi_raw):
    sbi_raw["schema_version"] = "99"
    with pytest.raises(SchemaError):
        statement_from_dict(sbi_raw)


@pytest.mark.parametrize("bad", [None, [], "statement", {"schema_version": "1"}])
def test_malformed_document_rejected(bad):
    with pytest.raises(SchemaError):
        statement_from_dict(bad)
