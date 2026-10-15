"""Execute a template's header rules against page text (deterministic)."""
import re

from bank_parser.extraction.lines import group_lines
from bank_parser.extraction.parsing import ParseError, header_date_formats, normalize_space, parse_amount, parse_date
from bank_parser.extraction.table import Problem

_MONEY_TARGETS = {"header.opening_balance", "header.closing_balance", "summary.total_debits", "summary.total_credits"}
_INT_TARGETS = {"summary.debit_count", "summary.credit_count"}
_DATE_TARGETS = {"header.period_start", "header.period_end"}
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD"}


def _flags(spec):
    f = 0
    for ch in spec:
        f |= {"i": re.IGNORECASE, "s": re.DOTALL, "m": re.MULTILINE}[ch]
    return f


def resolve_pages(rule_pages, page_count):
    out = []
    for p in rule_pages:
        n = p if p > 0 else page_count + 1 + p
        if 1 <= n <= page_count and n not in out:
            out.append(n)
    return out


def _convert(target, raw, date_formats):
    raw = normalize_space(raw)
    if not raw:
        return None
    if target in _MONEY_TARGETS:
        value, suffix = parse_amount(raw)
        if value is not None and suffix == "DR" and target.endswith("_balance"):
            value = -value
        return value
    if target in _INT_TARGETS:
        if not raw.isdigit():
            raise ParseError(f"not a count: {raw!r}")
        return int(raw)
    if target in _DATE_TARGETS:
        # Header dates are often printed differently from table dates (4- vs 2-digit year).
        return parse_date(raw, header_date_formats(date_formats))
    if target == "header.currency":
        return _CURRENCY_SYMBOLS.get(raw, raw)
    return raw


def _region_text(page, rule, tolerance):
    lines = group_lines(page.words, tolerance)
    start = 0
    if rule.after:
        rx = re.compile(rule.after, _flags(rule.flags))
        start = next((i + 1 for i, l in enumerate(lines) if rx.search(l.text)), None)
        if start is None:
            return None
    stop = len(lines)
    if rule.before:
        rx = re.compile(rule.before, _flags(rule.flags))
        stop = next((i for i in range(start, len(lines)) if rx.search(lines[i].text)), len(lines))
    picked = []
    strip = re.compile(rule.strip) if rule.strip else None
    for l in lines[start:stop]:
        text = " ".join(w.text for w in l.words if rule.x_min <= w.x0 and w.x1 <= rule.x_max)
        if strip:
            text = strip.sub("", text)
        text = normalize_space(text)
        if text:
            picked.append(text)
    lo, hi = rule.lines
    picked = picked[lo:hi]
    return rule.join.join(picked) if picked else None


def _block_text(page, rule, tolerance, max_up=2, max_down=6):
    """
    A labelled value that may continue on lines above or below it, e.g.
        "Account holder address:  12 MAIN ROAD"   with "NEAR THE PARK" and "560001" printed
    directly under (or over) "12". The value starts at the first word after the label and
    ends at a wide gap (the next column). A neighbouring line belongs to the value only if
    its words in that column start at the value's x, and nothing is printed in the label's
    column on that line. Positions are relative to the label's character width, so the rule
    is independent of the PDF's scale.
    """
    lines = group_lines(page.words, tolerance)
    rx = re.compile(rule.pattern, _flags(rule.flags))
    for i, line in enumerate(lines):
        m = rx.search(line.text)
        if not m:
            continue
        words, spans, pos = line.words, [], 0
        for w in words:
            spans.append((pos, pos + len(w.text)))
            pos += len(w.text) + 1
        label = [j for j, (a, b) in enumerate(spans) if a < m.end() and b > m.start()]
        if not label or label[-1] + 1 >= len(words):
            continue                                    # no value on the label's line
        chars = sum(len(words[j].text) for j in label)
        cw = sum(words[j].x1 - words[j].x0 for j in label) / max(chars, 1)
        value, col_end = [words[label[-1] + 1]], float("inf")
        for w in words[label[-1] + 2:]:
            if w.x0 - value[-1].x1 > 3 * cw:
                col_end = w.x0
                break
            value.append(w)
        value_x, label_x = value[0].x0, words[label[0]].x0
        height = max(line.bottom - line.top, cw)

        def column_words(other):
            ws = [w for w in other.words if w.x1 > label_x - cw and w.x0 < col_end]
            if not ws or ws[0].x0 < value_x - cw or abs(ws[0].x0 - value_x) > cw:
                return None
            return ws

        above, below = [], []
        edge = line
        for j in range(i - 1, max(i - 1 - max_up, -1), -1):
            ws = column_words(lines[j])
            if ws is None or edge.top - lines[j].bottom > 1.5 * height:
                break
            above.insert(0, ws)
            edge = lines[j]
        edge = line
        for j in range(i + 1, min(i + 1 + max_down, len(lines))):
            ws = column_words(lines[j])
            if ws is None or lines[j].top - edge.bottom > 1.5 * height:
                break
            below.append(ws)
            edge = lines[j]
        strip = re.compile(rule.strip) if rule.strip else None
        parts = []
        for ws in above + [value] + below:
            text = " ".join(w.text for w in ws)
            if strip:
                text = strip.sub("", text)
            text = normalize_space(text).strip(" ,;")
            if text:
                parts.append(text)
        if parts:
            return rule.join.join(parts)
    return None


def apply_header_rules(rules, pages_by_number, page_count, default_date_formats, tolerance=3.0, sources=None):
    """
    Returns ({target: value}, [Problem]). The first rule that yields a value
    for a target wins; later rules for the same target are fallbacks.
    `sources`, if given, receives {target: {"rule": index, "kind": ..., "page": n}}:
    which rule found each value (used by the self-review, D-024; values are not recorded).
    """
    values, problems = {}, []
    pending = {}        # parse problems; dropped when a later (fallback) rule fills the field (D-026)
    for index, rule in enumerate(rules):
        if all(t in values for t in rule.targets):
            continue
        fmts = rule.date_formats or default_date_formats
        for n in resolve_pages(rule.pages, page_count):
            page = pages_by_number.get(n)
            if page is None:
                continue
            if rule.kind == "regex":
                text = "\n".join(l.text for l in group_lines(page.words, tolerance))
                m = re.search(rule.pattern, text, _flags(rule.flags))
                if not m:
                    continue
                raws = m.groups() or (m.group(0),)
            elif rule.kind in ("region", "block"):
                region = (_region_text if rule.kind == "region" else _block_text)(page, rule, tolerance)
                if region is None:
                    continue
                raws = (region,)
            else:
                problems.append(Problem("unknown_rule_kind", rule.kind))
                break
            for target, raw in zip(rule.targets, raws):
                if target in values or raw is None:
                    continue
                try:
                    v = _convert(target, raw, fmts)
                except ParseError as e:
                    pending[target] = Problem("unparseable_header_value", f"{target}: {e}", n)
                    continue
                if v is not None:
                    values[target] = v
                    if sources is not None:
                        sources[target] = {"rule": index, "kind": rule.kind, "page": n}
            break
    problems += [p for t, p in pending.items() if t not in values]
    return values, problems
