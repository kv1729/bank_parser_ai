"""
Infer a transaction-table layout for a document no template matches.

Deterministic: column x-boundaries come from the ruled cell rectangles of the
transaction table; column roles come from header words when the header is
text, otherwise from the content of each column (dates, amounts, text). When
debit and credit cannot be told apart, both orders are proposed and the
balance chain decides (DECISIONS.md D-005, D-010).
"""
from collections import Counter
from dataclasses import dataclass, replace
import re

from bank_parser.extraction.parsing import CANDIDATE_DATE_FORMATS, is_amount, parse_date, ParseError
from bank_parser.extraction.table import assemble_rows
from bank_parser.templates.model import Column, TableSpec

MAX_LAYOUT_PAGES = 4        # pages scanned for the table header
MIN_COLUMNS = 4

_ROLE_WORDS = (
    ("value_date", re.compile(r"\bvalue\b", re.I)),
    ("txn_date", re.compile(r"\b(date|dt)\b", re.I)),
    ("balance", re.compile(r"\bbalance\b", re.I)),
    ("debit", re.compile(r"\b(withdrawal|withdrawals|debit|debits|dr)\b", re.I)),
    ("credit", re.compile(r"\b(deposit|deposits|credit|credits|cr)\b", re.I)),
    ("reference", re.compile(r"\b(ref|chq|cheque|instrument|utr)\b", re.I)),
    ("description", re.compile(r"\b(narration|description|particulars|details|remarks|transaction)\b", re.I)),
)


@dataclass(frozen=True)
class LayoutCandidate:
    spec: TableSpec
    header_page: int
    role_source: str          # "header_text" | "content" | "content+chain"
    note: str = ""


def _header_rows(cells_doc, page_count):
    """Yield (page, cells) for ruled rows with enough columns, top of each table first."""
    for n in range(1, min(page_count, MAX_LAYOUT_PAGES) + 1):
        for row in cells_doc.find_table_cells(n):
            cells = sorted(row, key=lambda c: c[0])
            if len(cells) >= MIN_COLUMNS:
                yield n, cells
                break   # only the first wide row of the page's table


HEADER_REACH = 14.0   # pts above the detected table top that may hold header text


def _band_words(page, cells):
    """Words of the header row. The ruled header cell can start above the
    table the finder reports (current SBI layout), so if the row itself holds
    no words, look up to one line above it."""
    top = min(c[1] for c in cells) - 1
    bottom = max(c[3] for c in cells) + 1
    words = [w for w in page.words if w.top >= top and w.bottom <= bottom]
    if not words:
        words = [w for w in page.words if top - HEADER_REACH <= w.top < top + 1 and w.bottom <= bottom]
    return words


def _label_roles(labels):
    roles = []
    used = set()
    for label in labels:
        role = None
        for r, rx in _ROLE_WORDS:
            if rx.search(label or ""):
                role = r
                break
        if role == "txn_date" and "txn_date" in used:
            role = "value_date"
        if role in used:
            role = None
        roles.append(role)
        if role:
            used.add(role)
    return roles


def _column_stats(rows, keys):
    stats = {}
    for k in keys:
        texts = [r.text(k) for r in rows]
        filled = [t for t in texts if t and t != "-"]
        n = max(len(filled), 1)
        dates = sum(1 for t in filled if _parses_any_date(t))
        amounts = sum(1 for t in filled if is_amount(t))
        stats[k] = {"fill": len(filled) / max(len(rows), 1), "date": dates / n, "amount": amounts / n,
                    "avg_len": sum(len(t) for t in filled) / n, "count": len(filled)}
    return stats


def _parses_any_date(text):
    for fmt in CANDIDATE_DATE_FORMATS:
        try:
            parse_date(text, (fmt,))
            return True
        except ParseError:
            continue
    return False


def choose_date_format(samples):
    """The candidate format that parses every sample, preferring chronologically ordered results."""
    best = None
    for fmt in CANDIDATE_DATE_FORMATS:
        try:
            parsed = [parse_date(s, (fmt,)) for s in samples]
        except ParseError:
            continue
        ordered = all(a <= b for a, b in zip(parsed, parsed[1:]))
        if ordered:
            return fmt
        best = best or fmt
    return best


def infer_layouts(cells_doc, doc):
    """Return LayoutCandidates (best first). `cells_doc` must support find_table_cells (pdfplumber)."""
    count = doc.page_count
    for page_no, cells in _header_rows(cells_doc, count):
        page = doc.page(page_no)
        bounds = [c[0] for c in cells] + [cells[-1][2]]
        band = _band_words(page, cells)
        labels = [" ".join(w.text for w in band if c[0] <= w.x_mid < c[2]) for c in cells]
        header_tokens = tuple(dict.fromkeys(w.text for w in band))
        min_tokens = min(2, len(header_tokens)) if header_tokens else 1

        # Provisional pass: first column is the date; keep every other column's text.
        cols = [Column("txn_date", bounds[0], bounds[1])] + [
            Column("ignore", bounds[i], bounds[i + 1], label=f"c{i}") for i in range(1, len(cells))]
        probe = TableSpec(tuple(cols), CANDIDATE_DATE_FORMATS, header_tokens, min_tokens)
        pages = [doc.page(n) for n in range(page_no, min(count, page_no + 2) + 1)]
        rows, _ = assemble_rows(pages, probe)
        if len(rows) < 2:
            continue
        date_samples = [r.text("txn_date") for r in rows if r.text("txn_date")]
        fmt = choose_date_format(date_samples)
        if fmt is None:
            continue
        keys = ["txn_date"] + [f"c{i}" for i in range(1, len(cells))]
        stats = _column_stats(rows, keys)

        roles = _label_roles(labels)
        source = "header_text"
        if not _roles_usable(roles, stats, keys):
            roles = _content_roles(stats, keys)
            source = "content"
        candidates = []
        for assignment, extra_note in _debit_credit_orders(roles, stats, keys, source):
            columns = tuple(Column(r or "ignore", bounds[i], bounds[i + 1],
                                   label=None if r else _safe_label(labels[i], i))
                            for i, r in enumerate(assignment))
            spec = TableSpec(columns, (fmt,), header_tokens, min_tokens)
            candidates.append(LayoutCandidate(spec, page_no, source + extra_note))
        if candidates:
            return candidates
    return []


def _safe_label(label, i):
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return s[:40] or f"column_{i}"


def _roles_usable(roles, stats, keys):
    """Header-text roles are trusted only if they include date + balance + both directions and agree with content."""
    need = {"txn_date", "debit", "credit"}
    if not need <= set(roles):
        return False
    for role, k in zip(roles, keys):
        if role in ("debit", "credit", "balance") and stats[k]["count"] and stats[k]["amount"] < 0.9:
            return False
        if role == "txn_date" and stats[k]["date"] < 0.9:
            return False
    return True


def _content_roles(stats, keys):
    roles = [None] * len(keys)
    date_cols = [i for i, k in enumerate(keys) if stats[k]["count"] and stats[k]["date"] >= 0.9]
    if date_cols:
        roles[date_cols[0]] = "txn_date"
    if len(date_cols) > 1:
        roles[date_cols[1]] = "value_date"
    amount_cols = [i for i, k in enumerate(keys) if roles[i] is None and stats[k]["count"] and stats[k]["amount"] >= 0.9]
    if amount_cols:
        dense = [i for i in amount_cols if stats[keys[i]]["fill"] >= 0.9]
        bal = dense[-1] if dense else amount_cols[-1]
        roles[bal] = "balance"
        rest = [i for i in amount_cols if i != bal]
        if len(rest) >= 2:
            roles[rest[0]], roles[rest[1]] = "debit", "credit"   # order resolved by the chain
    text_cols = [i for i, k in enumerate(keys) if roles[i] is None and stats[k]["count"]]
    if text_cols:
        desc = max(text_cols, key=lambda i: stats[keys[i]]["avg_len"])
        roles[desc] = "description"
        others = [i for i in text_cols if i != desc]
        if others:
            roles[others[0]] = "reference"
    if roles[0] is None:
        roles[0] = "txn_date"
    return roles


def _debit_credit_orders(roles, stats, keys, source):
    yield roles, ""
    if source == "content" and "debit" in roles and "credit" in roles:
        swapped = [("credit" if r == "debit" else "debit" if r == "credit" else r) for r in roles]
        yield swapped, "(debit/credit swapped)"
