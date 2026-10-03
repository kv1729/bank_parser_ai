"""Execute a template's header rules against page text (deterministic)."""
import re

from bank_parser.extraction.lines import group_lines
from bank_parser.extraction.parsing import CANDIDATE_DATE_FORMATS, ParseError, normalize_space, parse_amount, parse_date
from bank_parser.extraction.table import Problem

_MONEY_TARGETS = {"header.opening_balance", "header.closing_balance", "summary.total_debits", "summary.total_credits"}
_INT_TARGETS = {"summary.debit_count", "summary.credit_count"}
_DATE_TARGETS = {"header.period_start", "header.period_end"}


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
        return parse_date(raw, tuple(date_formats) + CANDIDATE_DATE_FORMATS)
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


def apply_header_rules(rules, pages_by_number, page_count, default_date_formats, tolerance=3.0):
    """
    Returns ({target: value}, [Problem]). The first rule that yields a value
    for a target wins; later rules for the same target are fallbacks.
    """
    values, problems = {}, []
    for rule in rules:
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
            elif rule.kind == "region":
                region = _region_text(page, rule, tolerance)
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
                    problems.append(Problem("unparseable_header_value", f"{target}: {e}", n))
                    continue
                if v is not None:
                    values[target] = v
            break
    return values, problems
