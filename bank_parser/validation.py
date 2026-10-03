"""
Deterministic validation of parsed statements.

Validators never raise on bad statement data: malformed values are reported
as FAIL issues so a corrupted parse can never pass silently.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


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
