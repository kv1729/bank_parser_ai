"""
Deterministic "word_columns" table extraction.

Rows are assembled from positioned words: each word is assigned to a column
by its horizontal midpoint, a new row starts when the transaction-date column
begins a new date, and lines without a date continue the current row (multi-
line descriptions, dates wrapped onto two lines, rows split across pages).
The table on a page ends at the first line that cannot belong to it (a footer).

This replaces pdfplumber's ruled-table finder, which silently dropped a row
split across pages (DECISIONS.md D-002, D-010).
"""
from dataclasses import dataclass, field

from bank_parser.extraction.lines import group_lines
from bank_parser.extraction.parsing import (
    ParseError,
    is_complete_date,
    looks_like_date_start,
    normalize_space,
    parse_amount,
    parse_date,
)
from bank_parser.core.schema import Transaction

_STRADDLE_MARGIN = 2.0


@dataclass
class RawRow:
    page: int
    cells: dict = field(default_factory=dict)   # role -> list of text fragments

    def add(self, role, text):
        self.cells.setdefault(role, []).append(text)

    def text(self, role):
        return normalize_space(" ".join(self.cells.get(role, ())))


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    page: int | None = None
    row_index: int | None = None


def _assign(line, columns):
    """Map a line's words to columns. Returns None if the line does not fit the grid."""
    left, right = columns[0].x0 - _STRADDLE_MARGIN, columns[-1].x1 + _STRADDLE_MARGIN
    cells = {}
    for w in line.words:
        if w.x0 < left or w.x1 > right:
            return None
        col = next((c for c in columns if c.x0 <= w.x_mid < c.x1), None)
        if col is None:
            return None
        # A word crossing well into a neighbouring column is not table content.
        if w.x0 < col.x0 - _STRADDLE_MARGIN * 3 or w.x1 > col.x1 + _STRADDLE_MARGIN * 3:
            return None
        cells.setdefault(col, []).append(w.text)
    return cells


def _is_header_line(line, spec):
    tokens = set(spec.header_tokens)
    words = [w.text for w in line.words]
    return bool(tokens) and len(words) >= spec.min_header_tokens and all(w in tokens for w in words)


# A row's text can start *above* the line holding its date: in the current SBI
# layout the first description line ("WDL TFR", "DEP TFR") sits just above the
# date and overlaps it vertically. Lines like that belong to the row below, not
# to the end of the row above. They are recognised by geometry only:
#   - the block's last line touches or overlaps the next date line, and
#   - the block's lines are packed together but separated from what precedes.
# Layouts whose continuation lines are clearly spaced from the next date line
# (synthetic SBI 8-9 pt, HDFC 7-8 pt) never match (DECISIONS.md D-018).
LEAD_TOUCH_PT = 0.5     # gap from block to the date line: <= this (or negative = overlap)
PACKED_GAP_PT = 2.0     # gap between lines of one cell: <= this


def _split_lead_lines(pending, anchor_bottom, date_line):
    """Split pending undated lines into (belongs to previous row, leads the next row)."""
    if not pending or date_line.top - pending[-1][0].bottom > LEAD_TOUCH_PT:
        return pending, []
    j = len(pending) - 1
    while j > 0 and pending[j][0].top - pending[j - 1][0].bottom <= PACKED_GAP_PT:
        j -= 1
    before = pending[j - 1][0].bottom if j > 0 else anchor_bottom
    if before is not None and pending[j][0].top - before <= PACKED_GAP_PT:
        return pending, []          # packed all the way back to the previous row: cannot tell, keep as before
    return pending[:j], pending[j:]


def _add_cells(row, cells):
    for col, texts in cells.items():
        key = col.label if col.role == "ignore" and col.label else col.role
        row.add(key, " ".join(texts))


def assemble_rows(pages, spec):
    """Turn PageLayouts into RawRows. `pages` may be a generator (one page in memory at a time)."""
    columns = tuple(sorted(spec.columns, key=lambda c: c.x0))
    date_col = next(c for c in columns if c.role == "txn_date")
    rows, problems = [], []
    current = None
    for page in pages:
        lines = group_lines(page.words, spec.line_tolerance)
        in_table = not spec.header_on_every_page
        seen_header = False
        pending = []                # undated lines not yet assigned to a row
        anchor_bottom = None        # bottom of the last line before `pending`

        def flush_pending():
            nonlocal pending
            for _, cells in pending:
                if current is not None:
                    _add_cells(current, cells)
            pending = []

        for line in lines:
            if not in_table:
                if _is_header_line(line, spec):
                    seen_header = True
                    anchor_bottom = line.bottom
                    continue
                if not seen_header:
                    continue
                in_table = True
            elif _is_header_line(line, spec) and current is None:
                anchor_bottom = line.bottom
                continue
            cells = _assign(line, columns)
            if cells is None:
                if seen_header and current is None and not rows:
                    continue  # stray text between header and first row
                break         # footer: table on this page has ended
            date_text = " ".join(cells.get(date_col, ()))
            if not date_text:
                if current is None and not rows and not pending and anchor_bottom is None:
                    continue
                pending.append((line, cells))
                continue
            if current is not None and not is_complete_date(current.text("txn_date"), spec.date_formats):
                flush_pending()            # date wrapped onto a second line: keep filling current row
                _add_cells(current, cells)
            elif looks_like_date_start(date_text):
                to_previous, lead = _split_lead_lines(pending, anchor_bottom, line)
                pending = to_previous
                flush_pending()
                if current is not None:
                    rows.append(current)
                current = RawRow(page.number)
                for _, lead_cells in lead:
                    _add_cells(current, lead_cells)
                _add_cells(current, cells)
            else:
                break      # non-date text in the date column: footer
            anchor_bottom = line.bottom
        flush_pending()
        # leave `current` open: its description may continue on the next page
    if current is not None:
        rows.append(current)
    return rows, problems


def _money(row, role, spec, problems, index):
    try:
        value, suffix = parse_amount(row.text(role), spec.empty_tokens)
    except ParseError as e:
        problems.append(Problem("unparseable_amount", f"{role}: {e}", row.page, index))
        return None, None
    return value, suffix


def rows_to_transactions(rows, spec):
    """Normalize RawRows into Transactions; unparseable values are reported, not guessed."""
    problems, txns = [], []
    roles = {c.role for c in spec.columns}
    extra_keys = [c.label for c in spec.columns if c.role == "ignore" and c.label]
    for i, row in enumerate(rows):
        try:
            txn_date = parse_date(row.text("txn_date"), spec.date_formats)
        except ParseError as e:
            problems.append(Problem("unparseable_date", str(e), row.page, i))
            continue
        value_date = None
        if "value_date" in roles and row.text("value_date"):
            try:
                value_date = parse_date(row.text("value_date"), spec.date_formats)
            except ParseError as e:
                problems.append(Problem("unparseable_value_date", str(e), row.page, i))

        debit = credit = None
        if spec.amount_mode == "columns":
            debit, _ = _money(row, "debit", spec, problems, i)
            credit, _ = _money(row, "credit", spec, problems, i)
        else:
            amount, suffix = _money(row, "amount", spec, problems, i)
            marker = (row.text("dr_cr") or suffix or "").upper().rstrip(".")
            if amount is not None:
                if spec.amount_mode == "signed":
                    debit, credit = (-amount, None) if amount < 0 else (None, amount)
                elif marker in ("DR", "D"):
                    debit = amount
                elif marker in ("CR", "C"):
                    credit = amount
                else:
                    problems.append(Problem("unknown_direction", "amount has no Dr/Cr marker", row.page, i))

        balance = None
        if "balance" in roles:
            balance, suffix = _money(row, "balance", spec, problems, i)
            if balance is not None and suffix == "DR":
                balance = -balance       # overdrawn balance printed as "123.00DR"

        reference = row.text("reference") or None
        if reference in spec.empty_tokens:
            reference = None
        txns.append(Transaction(
            row_index=len(txns), txn_date=txn_date, value_date=value_date,
            description=row.text("description"), reference=reference,
            debit=debit, credit=credit, balance=balance, page=row.page,
            extra={k: row.text(k) for k in extra_keys if row.text(k)},
        ))
    return tuple(txns), problems
