"""
Checks for text, which arithmetic cannot verify (Stage 10.5, D-025; CHECKPOINT B5).

Warnings only: these are signs that text was lost or moved, not proof. They use
the statement and the table reader's trace (positions and word counts), and
report codes, rows and counts, never the text itself.

  text_not_captured         a line inside the table was skipped, or the table on a page
                            ended at a line that is followed by more dated lines
  empty_description         a transaction without a description
  description_start_at_end  a row's description ends with the words that start most other
                            descriptions: the next row's first line has probably moved up
                            into it (the failure mode of D-018)
  label_in_value            a label word (Address, Branch, IFSC...) inside an account value
"""
from collections import Counter
import re

from bank_parser.core.validation import CheckResult, Issue, Severity, Status

CHECK = "text"
MIN_ROWS_FOR_PATTERN = 5          # T3 needs enough rows to know how descriptions start
START_SHARE = 0.3                 # a start shared by at least this share of rows is "typical"
LABEL_WORDS = ("account", "address", "branch", "ifsc", "customer", "statement", "period", "mobile",
               "email", "phone", "micr", "nominee", "balance")
TEXT_FIELDS = ("account_holder_name", "account_holder_address", "branch", "bank_address")
_WORD = re.compile(r"[A-Za-z]+")
# Words that open a transaction description in Indian and US statements. Two of them at the END of a
# description ("... WDL TFR") mean the next row's first line has moved up (D-018). "BY"/"TO" are left
# out: "TRANSFER TO" ends descriptions legitimately.
TXN_TYPE_WORDS = {"WDL", "DEP", "TFR", "UPI", "NEFT", "IMPS", "RTGS", "ATM", "POS", "ACH", "ECS", "NACH",
                  "CHQ", "MMT", "INB", "CLG", "DR", "CR"}


def _get(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _starts(descriptions):
    """Typical first one or two words of descriptions, upper-cased, shared by >= START_SHARE of rows."""
    found = []
    for k in (2, 1):
        counts = Counter(" ".join(d.split()[:k]).upper() for d in descriptions if len(d.split()) > k)
        found += [s for s, c in counts.items() if c >= START_SHARE * len(descriptions) and _WORD.search(s)]
    return found


def text_check(statement, trace=None):
    """CheckResult("text") for a Statement (or its dict form) and the table reader's trace."""
    issues, checked = [], 0
    header = _get(statement, "header") or {}
    txns = list(_get(statement, "transactions") or [])
    trace = trace or {}

    # T1: text inside the table that belongs to no row.
    for d in trace.get("dropped", []):
        checked += 1
        issues.append(Issue("text_not_captured", Severity.WARN,
                            f"a line of {d['words']} word(s) inside the table belongs to no row",
                            d.get("near_row"), d.get("page")))
    for e in trace.get("early_end", []):
        issues.append(Issue("text_not_captured", Severity.WARN,
                            f"the table on this page ended early: {e['rows_after']} dated line(s) after it were not read",
                            e.get("near_row") if (e.get("near_row") or -1) >= 0 else None, e.get("page")))

    # T2: rows without a description.
    descriptions = []
    for pos, t in enumerate(txns):
        checked += 1
        text = (_get(t, "description") or "").strip()
        row, page = _get(t, "row_index"), _get(t, "page")
        if not text:
            issues.append(Issue("empty_description", Severity.WARN, "row has no description",
                                pos if row is None else row, page))
        descriptions.append(text)

    # T3: the start of a description at the end of another one.
    filled = [d for d in descriptions if d]
    starts = _starts(filled) if len(filled) >= MIN_ROWS_FOR_PATTERN else []
    for pos, (t, d) in enumerate(zip(txns, descriptions)):
        up = d.upper()
        tail = up.split()[-2:]
        typical = any(up.endswith(" " + s) for s in starts)
        type_words = len(up.split()) > 2 and len(tail) == 2 and all(w in TXN_TYPE_WORDS for w in tail)
        if typical or type_words:
            row = _get(t, "row_index")
            issues.append(Issue("description_start_at_end", Severity.WARN,
                                "description ends with words that usually start one; "
                                "the next row's first line may have moved here",
                                pos if row is None else row, _get(t, "page")))

    # T4: label words inside account values.
    for name in TEXT_FIELDS:
        value = _get(header, name)
        if not value:
            continue
        checked += 1
        words = {w.lower() for w in _WORD.findall(str(value))}
        hits = sorted(words & set(LABEL_WORDS))
        if hits:
            issues.append(Issue("label_in_value", Severity.WARN,
                                f"{name} contains label word(s): {', '.join(hits)}"))

    status = Status.WARN if issues else (Status.PASS if checked else Status.SKIP)
    return CheckResult(CHECK, status, tuple(issues), checked)


def text_check_to_dict(check):
    return {"check": check.check, "status": check.status.value, "rows_checked": check.rows_checked,
            "issues": [{"code": i.code, "severity": i.severity.value, "message": i.message,
                        "row_index": i.row_index, "page": i.page} for i in check.issues]}


def with_text_check(report_dict, check):
    """The stored report with the text check added; PASS becomes WARN when text was questioned."""
    out = dict(report_dict)
    out["checks"] = [c for c in report_dict.get("checks", []) if c.get("check") != CHECK] + [text_check_to_dict(check)]
    if check.status is Status.WARN and out.get("status") == "PASS":
        out["status"] = "WARN"
    return out
