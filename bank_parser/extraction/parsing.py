"""Parsing of printed dates and amounts into exact types."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

# Formats tried when a layout's date format is not yet known (day-first:
# Indian statements). Order matters only for ambiguous inputs.
CANDIDATE_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y", "%d %B %Y", "%Y-%m-%d",
)

_DATE_START_RE = re.compile(r"^\d{1,2}(?:$|[\s/.\-])|^\d{4}-\d{2}-\d{2}")
_AMOUNT_RE = re.compile(r"^\(?-?[\d,]*\d(?:\.\d+)?\)?$")
_SUFFIX_RE = re.compile(r"\s*(CR|DR|Cr|Dr|cr|dr)\.?$")   # no \b: "9,999.99CR" has no word boundary


class ParseError(ValueError):
    pass


def normalize_space(text):
    return " ".join((text or "").split())


def parse_date(text, formats):
    text = normalize_space(text)
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"unparseable date {text!r}")


def is_complete_date(text, formats):
    try:
        parse_date(text, formats)
        return True
    except ParseError:
        return False


def looks_like_date_start(text):
    return bool(_DATE_START_RE.match(normalize_space(text)))


def parse_amount(text, empty_tokens=("", "-")):
    """
    Parse a printed amount such as "1,20,749.89", "-", "9,999.99CR", "(12.00)".
    Returns (Decimal | None, suffix | None). A DR suffix or parentheses do not
    change the sign here; the caller decides what the suffix means.
    """
    raw = normalize_space(text)
    if raw in empty_tokens:
        return None, None
    suffix = None
    m = _SUFFIX_RE.search(raw)
    if m:
        suffix = m.group(1).upper()
        raw = raw[: m.start()].strip()
    if not _AMOUNT_RE.match(raw):
        raise ParseError(f"unparseable amount {text!r}")
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ParseError(f"unparseable amount {text!r}") from None
    if not value.is_finite():
        raise ParseError(f"unparseable amount {text!r}")
    return (-value if negative else value), suffix


def is_amount(text):
    try:
        v, _ = parse_amount(text, empty_tokens=())
        return v is not None
    except ParseError:
        return False
