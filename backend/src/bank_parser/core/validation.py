"""
Deterministic validation of parsed statements.

Validators never raise on bad statement data: malformed values are reported
as FAIL issues so a corrupted parse can never pass silently.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import re


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    # The document does not provide the data this check needs (e.g. no
    # running balance). Not a failure: validate only what the document offers.
    SKIP = "SKIP"


class Severity(str, Enum):
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    message: str
    row_index: int | None = None
    page: int | None = None


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: Status
    issues: tuple[Issue, ...] = field(default_factory=tuple)
    rows_checked: int = 0


def _money_problem(value):
    """Return a reason string if value is not a usable money amount, else None."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        return f"expected Decimal, got {type(value).__name__}"
    if not value.is_finite():
        return f"non-finite amount {value}"
    return None


def _status_for(issues, rows_checked):
    if any(i.severity is Severity.FAIL for i in issues):
        return Status.FAIL
    if issues:
        return Status.WARN
    return Status.PASS if rows_checked else Status.SKIP


def validate_balance_chain(statement):
    """
    Check that every row satisfies: previous balance - debit + credit = balance.

    The chain starts from header.opening_balance when present; otherwise the
    first row's stated balance is taken as given and the first row is unchecked.
    A row with no stated balance is carried forward using the computed balance
    and verified cumulatively at the next stated balance.
    """
    check = "balance_chain"
    issues = []

    try:
        txns = tuple(statement.transactions)
        opening = statement.header.opening_balance
    except (AttributeError, TypeError) as e:
        return CheckResult(check, Status.FAIL,
                           (Issue("malformed_statement", Severity.FAIL, str(e)),))

    if not txns:
        return CheckResult(check, Status.SKIP)
    if all(getattr(t, "balance", None) is None for t in txns):
        return CheckResult(check, Status.SKIP,
                           (Issue("no_running_balance", Severity.WARN,
                                  "statement provides no running balance"),))

    problem = _money_problem(opening)
    if problem:
        issues.append(Issue("malformed_amount", Severity.FAIL, f"opening_balance: {problem}"))
        opening = None

    prev = opening
    if prev is None:
        issues.append(Issue("no_opening_balance", Severity.WARN,
                            "opening balance unavailable; first row's balance taken as given",
                            row_index=0))

    rows_checked = 0
    for pos, t in enumerate(txns):
        row = getattr(t, "row_index", pos)
        page = getattr(t, "page", None)
        debit = getattr(t, "debit", None)
        credit = getattr(t, "credit", None)
        balance = getattr(t, "balance", None)

        bad = [(name, _money_problem(v)) for name, v in
               (("debit", debit), ("credit", credit), ("balance", balance))]
        bad = [(n, p) for n, p in bad if p]
        if bad:
            for name, p in bad:
                issues.append(Issue("malformed_amount", Severity.FAIL, f"{name}: {p}", row, page))
            # Cannot trust this row; resynchronise on its balance if usable.
            prev = balance if _money_problem(balance) is None else None
            continue

        if debit is None and credit is None:
            issues.append(Issue("missing_amount", Severity.FAIL,
                                "row has neither debit nor credit", row, page))
            prev = balance
            continue
        if (debit is not None and debit < 0) or (credit is not None and credit < 0):
            issues.append(Issue("negative_amount", Severity.FAIL,
                                "debit/credit must be non-negative", row, page))
            prev = balance
            continue

        if prev is None:
            # Chain start (no opening balance, or resync after a bad row).
            prev = balance
            continue

        expected = prev - (debit or Decimal(0)) + (credit or Decimal(0))
        if balance is None:
            issues.append(Issue("balance_missing", Severity.WARN,
                                "row has no stated balance; carried forward", row, page))
            prev = expected
            continue

        rows_checked += 1
        if expected != balance:
            issues.append(Issue("balance_mismatch", Severity.FAIL,
                                f"expected balance {expected}, stated {balance}", row, page))
        # Continue from the stated balance so one bad amount yields one issue.
        prev = balance

    return CheckResult(check, _status_for(issues, rows_checked), tuple(issues), rows_checked)


def _usable(value):
    return value is not None and _money_problem(value) is None


def validate_reconciliation(statement):
    """
    Compare extracted transactions with totals the bank printed: closing
    balance, total debits/credits and debit/credit counts. Each figure is
    checked only when the document provides it.
    """
    check = "reconciliation"
    issues, checked = [], 0
    try:
        txns = tuple(statement.transactions)
        header = statement.header
        summary = statement.summary
    except (AttributeError, TypeError) as e:
        return CheckResult(check, Status.FAIL, (Issue("malformed_statement", Severity.FAIL, str(e)),))

    def compare(code, extracted, printed):
        nonlocal checked
        checked += 1
        if extracted != printed:
            issues.append(Issue(code, Severity.FAIL, f"extracted {extracted}, statement prints {printed}"))

    if _usable(header.closing_balance) and txns and _usable(txns[-1].balance):
        compare("closing_balance_mismatch", txns[-1].balance, header.closing_balance)
    if _usable(header.closing_balance) and _usable(header.opening_balance):
        debits = [t.debit for t in txns if _usable(t.debit)]
        credits = [t.credit for t in txns if _usable(t.credit)]
        compare("net_movement_mismatch", header.opening_balance - sum(debits, Decimal(0)) + sum(credits, Decimal(0)),
                header.closing_balance)
    if summary is not None:
        debits = [t.debit for t in txns if _usable(t.debit)]
        credits = [t.credit for t in txns if _usable(t.credit)]
        if _usable(summary.total_debits):
            compare("total_debits_mismatch", sum(debits, Decimal(0)), summary.total_debits)
        if _usable(summary.total_credits):
            compare("total_credits_mismatch", sum(credits, Decimal(0)), summary.total_credits)
        if isinstance(summary.debit_count, int):
            compare("debit_count_mismatch", len(debits), summary.debit_count)
        if isinstance(summary.credit_count, int):
            compare("credit_count_mismatch", len(credits), summary.credit_count)
    return CheckResult(check, _status_for(issues, checked), tuple(issues), checked)


def validate_rows(statement):
    """Per-row structure: exactly one of debit/credit, dates in order and inside the period."""
    check = "rows"
    issues = []
    try:
        txns = tuple(statement.transactions)
        start, end = statement.header.period_start, statement.header.period_end
    except (AttributeError, TypeError) as e:
        return CheckResult(check, Status.FAIL, (Issue("malformed_statement", Severity.FAIL, str(e)),))
    prev_date = None
    for pos, t in enumerate(txns):
        row, page = getattr(t, "row_index", pos), getattr(t, "page", None)
        d, c = getattr(t, "debit", None), getattr(t, "credit", None)
        if d is not None and c is not None:
            issues.append(Issue("both_debit_and_credit", Severity.FAIL, "row has both debit and credit", row, page))
        when = getattr(t, "txn_date", None)
        if when is None:
            issues.append(Issue("missing_date", Severity.FAIL, "row has no transaction date", row, page))
            continue
        if prev_date is not None and when < prev_date:
            issues.append(Issue("date_out_of_order", Severity.WARN, "date earlier than previous row", row, page))
        if (start and when < start) or (end and when > end):
            issues.append(Issue("date_outside_period", Severity.WARN, "date outside statement period", row, page))
        prev_date = when
    return CheckResult(check, _status_for(issues, len(txns)), tuple(issues), len(txns))


_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def validate_header(statement):
    """Format checks on header fields the document provides."""
    check = "header"
    issues, checked = [], 0
    try:
        h = statement.header
    except AttributeError as e:
        return CheckResult(check, Status.FAIL, (Issue("malformed_statement", Severity.FAIL, str(e)),))
    if h.ifsc is not None:
        checked += 1
        if not _IFSC_RE.match(h.ifsc):
            issues.append(Issue("invalid_ifsc", Severity.FAIL, "IFSC does not match AAAA0XXXXXX"))
    if h.account_number is not None:
        checked += 1
        if not re.fullmatch(r"[0-9X*]{6,20}", h.account_number):
            issues.append(Issue("invalid_account_number", Severity.FAIL, "account number is not 6-20 digits"))
    if h.period_start and h.period_end:
        checked += 1
        if h.period_start > h.period_end:
            issues.append(Issue("period_reversed", Severity.FAIL, "statement period starts after it ends"))
    for name in ("account_holder_name", "account_number", "period_start", "period_end"):
        if getattr(h, name) is None:
            issues.append(Issue("missing_header_field", Severity.WARN, f"{name} not extracted"))
    return CheckResult(check, _status_for(issues, checked), tuple(issues), checked)


@dataclass(frozen=True)
class ValidationReport:
    status: Status
    checks: tuple[CheckResult, ...]

    @property
    def verified(self):
        """True when at least one arithmetic invariant passed and nothing failed."""
        return self.status is not Status.FAIL and any(
            c.check in ("balance_chain", "reconciliation") and c.status is Status.PASS for c in self.checks)


def validate_statement(statement):
    """Run every validator. Overall FAIL if any check fails; WARN if nothing arithmetic could be verified."""
    checks = (validate_balance_chain(statement), validate_reconciliation(statement),
              validate_rows(statement), validate_header(statement))
    if any(c.status is Status.FAIL for c in checks):
        status = Status.FAIL
    elif not any(c.check in ("balance_chain", "reconciliation") and c.status is Status.PASS for c in checks):
        status = Status.WARN  # nothing arithmetic verified the extraction
    elif any(c.status is Status.WARN for c in checks):
        status = Status.WARN
    else:
        status = Status.PASS
    return ValidationReport(status, checks)


def report_to_dict(report):
    return {
        "status": report.status.value,
        "verified": report.verified,
        "checks": [{
            "check": c.check, "status": c.status.value, "rows_checked": c.rows_checked,
            "issues": [{"code": i.code, "severity": i.severity.value, "message": i.message,
                        "row_index": i.row_index, "page": i.page} for i in c.issues],
        } for c in report.checks],
    }
