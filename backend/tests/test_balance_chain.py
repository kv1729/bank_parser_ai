from dataclasses import replace
from decimal import Decimal

import pytest

from bank_parser import Status, statement_from_dict, validate_balance_chain


def _with_row(statement, index, **changes):
    txns = list(statement.transactions)
    txns[index] = replace(txns[index], **changes)
    return replace(statement, transactions=tuple(txns))


def _codes(result):
    return [i.code for i in result.issues]


def test_sbi_ground_truth_passes(sbi_statement):
    result = validate_balance_chain(sbi_statement)
    assert result.status is Status.PASS
    assert result.issues == ()
    assert result.rows_checked == 32


@pytest.mark.parametrize("index", range(32))
def test_any_single_changed_amount_fails(sbi_statement, index):
    t = sbi_statement.transactions[index]
    field = "debit" if t.debit is not None else "credit"
    corrupted = _with_row(sbi_statement, index, **{field: getattr(t, field) + Decimal("0.01")})

    result = validate_balance_chain(corrupted)

    assert result.status is Status.FAIL
    assert _codes(result) == ["balance_mismatch"]
    assert result.issues[0].row_index == index


def test_changed_balance_fails_at_that_row_and_next(sbi_statement):
    t = sbi_statement.transactions[5]
    result = validate_balance_chain(_with_row(sbi_statement, 5, balance=t.balance + 1))
    assert result.status is Status.FAIL
    assert [i.row_index for i in result.issues] == [5, 6]


def test_debit_credit_swapped_fails(sbi_statement):
    t = sbi_statement.transactions[0]
    result = validate_balance_chain(_with_row(sbi_statement, 0, debit=None, credit=t.debit))
    assert result.status is Status.FAIL


def test_wrong_opening_balance_fails(sbi_statement):
    header = replace(sbi_statement.header, opening_balance=Decimal("75222.70"))
    result = validate_balance_chain(replace(sbi_statement, header=header))
    assert result.status is Status.FAIL
    assert result.issues[0].row_index == 0


@pytest.mark.parametrize("bad", [88.5, "88.50", Decimal("NaN"), Decimal("Infinity")])
def test_malformed_amount_fails_safely(sbi_statement, bad):
    result = validate_balance_chain(_with_row(sbi_statement, 0, debit=bad))
    assert result.status is Status.FAIL
    assert "malformed_amount" in _codes(result)


def test_row_without_any_amount_fails(sbi_statement):
    result = validate_balance_chain(_with_row(sbi_statement, 0, debit=None))
    assert result.status is Status.FAIL
    assert "missing_amount" in _codes(result)


def test_negative_amount_fails(sbi_statement):
    result = validate_balance_chain(_with_row(sbi_statement, 0, debit=Decimal("-88.50")))
    assert result.status is Status.FAIL
    assert "negative_amount" in _codes(result)


@pytest.mark.parametrize("bad", [None, object(), "statement"])
def test_malformed_statement_object_fails_safely(bad):
    result = validate_balance_chain(bad)
    assert result.status is Status.FAIL
    assert _codes(result) == ["malformed_statement"]


def test_missing_balance_is_carried_forward(sbi_statement):
    result = validate_balance_chain(_with_row(sbi_statement, 5, balance=None))
    assert result.status is Status.WARN
    assert _codes(result) == ["balance_missing"]


def test_missing_balance_still_catches_corruption_next_row(sbi_statement):
    s = _with_row(sbi_statement, 5, balance=None)
    s = _with_row(s, 5, credit=s.transactions[5].credit + 1)
    result = validate_balance_chain(s)
    assert result.status is Status.FAIL
    assert "balance_mismatch" in _codes(result)


def test_no_opening_balance_warns_but_checks_the_rest(sbi_statement):
    header = replace(sbi_statement.header, opening_balance=None)
    result = validate_balance_chain(replace(sbi_statement, header=header))
    assert result.status is Status.WARN
    assert _codes(result) == ["no_opening_balance"]
    assert result.rows_checked == 31


def test_statement_without_running_balance_is_skipped_not_failed(sbi_raw):
    for t in sbi_raw["transactions"]:
        t["balance"] = None
    result = validate_balance_chain(statement_from_dict(sbi_raw))
    assert result.status is Status.SKIP


def test_empty_statement_is_skipped(sbi_statement):
    result = validate_balance_chain(replace(sbi_statement, transactions=()))
    assert result.status is Status.SKIP


# --- reconciliation / rows / header validators -------------------------------------------------

from bank_parser.core.schema import StatementSummary
from bank_parser.core.validation import validate_header, validate_reconciliation, validate_rows, validate_statement


def _summary_for(statement):
    debits = [t.debit for t in statement.transactions if t.debit is not None]
    credits = [t.credit for t in statement.transactions if t.credit is not None]
    return StatementSummary(sum(debits), sum(credits), len(debits), len(credits))


def test_reconciliation_passes_with_true_totals(sbi_statement):
    s = replace(sbi_statement, summary=_summary_for(sbi_statement),
                header=replace(sbi_statement.header, closing_balance=sbi_statement.transactions[-1].balance))
    result = validate_reconciliation(s)
    assert result.status is Status.PASS and result.rows_checked == 6


def test_reconciliation_catches_a_missing_row(sbi_statement):
    s = replace(sbi_statement, summary=_summary_for(sbi_statement))
    dropped = replace(s, transactions=s.transactions[:14] + s.transactions[15:])
    result = validate_reconciliation(dropped)
    assert result.status is Status.FAIL
    assert {"total_debits_mismatch", "debit_count_mismatch"} <= set(_codes(result))


def test_reconciliation_skipped_when_document_prints_no_totals(sbi_statement):
    assert validate_reconciliation(sbi_statement).status is Status.SKIP


def test_rows_flag_both_debit_and_credit(sbi_statement):
    s = _with_row(sbi_statement, 0, credit=Decimal("1.00"))
    assert "both_debit_and_credit" in _codes(validate_rows(s))


def test_header_flags_invalid_ifsc(sbi_statement):
    s = replace(sbi_statement, header=replace(sbi_statement.header, ifsc="SBIN1234"))
    assert validate_header(s).status is Status.FAIL


def test_overall_report(sbi_statement):
    report = validate_statement(sbi_statement)
    assert report.status is Status.PASS and report.verified
    assert validate_statement(_with_row(sbi_statement, 3, credit=Decimal("1"))).status is Status.FAIL
