"""Parsing of printed dates and amounts into exact types."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

# Formats tried when a layout's date format is not yet known. Day-first (Indian
# statements) comes first, so an ambiguous date such as 01/05/2024 reads as before;
# month-first (US) formats follow (D-026). Order matters only for ambiguous inputs.
DAY_FIRST_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y", "%d %B %Y", "%Y-%m-%d",
)
MONTH_FIRST_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%b. %d, %Y",
)
# US statements often print rows without the year ("01/15", "Jan 15"); the engine takes the
# year from the statement period (engine.resolve_years).
YEARLESS_FORMATS = ("%m/%d", "%b %d")
CANDIDATE_DATE_FORMATS = DAY_FIRST_FORMATS + MONTH_FIRST_FORMATS + YEARLESS_FORMATS
YEARLESS_YEAR = 1904          # placeholder year of a year-less date (a leap year, so Feb 29 parses)

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_START_RE = re.compile(r"^\d{1,2}(?:$|[\s/.\-])|^\d{4}-\d{2}-\d{2}|^" + _MONTHS + r"\s+\d{1,2}\b", re.I)
_AMOUNT_RE = re.compile(r"^\(?-?[\d,]*\d(?:\.\d+)?\)?$")
_SUFFIX_RE = re.compile(r"\s*(CR|DR|Cr|Dr|cr|dr)\.?$")   # no \b: "9,999.99CR" has no word boundary
# Currency markers printed with amounts: "₹ 1,000.00", "Rs. 50", "INR 10", "$12.00", "12.00 USD" (D-020).
_CURRENCY_RE = re.compile(r"(?:₹|Rs\.?|INR|USD|US\$|\$)", re.IGNORECASE)


class ParseError(ValueError):
    pass


def normalize_space(text):
    return " ".join((text or "").split())


def month_first(formats):
    """True when a layout's date formats are month-first (US): header dates are then read that way too."""
    return any(f in MONTH_FIRST_FORMATS or f in YEARLESS_FORMATS for f in formats)


def header_date_formats(table_formats):
    """Formats for header dates: the table's own, then candidates in the layout's locale order."""
    rest = (MONTH_FIRST_FORMATS + YEARLESS_FORMATS + DAY_FIRST_FORMATS) if month_first(table_formats)         else CANDIDATE_DATE_FORMATS
    return tuple(table_formats) + tuple(f for f in rest if f not in table_formats)


def parse_date(text, formats):
    text = normalize_space(text)
    for fmt in formats:
        try:
            if fmt in YEARLESS_FORMATS:      # no year printed: a placeholder, resolved from the period
                return datetime.strptime(f"{text} {YEARLESS_YEAR}", f"{fmt} %Y").date()
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
    if _CURRENCY_RE.search(raw):
        raw = normalize_space(_CURRENCY_RE.sub(" ", raw))
        raw = raw.replace("- ", "-").replace("( ", "(").replace(" )", ")")   # "-₹ 5" -> "-5", "(₹ 5)" -> "(5)"
        if raw in empty_tokens:
            return None, None
    trailing_minus = len(raw) > 1 and raw.endswith("-") and raw[-2].isdigit()   # "45.10-" (some US banks)
    if trailing_minus:
        raw = raw[:-1]
    suffix = None
    m = _SUFFIX_RE.search(raw)
    if m:
        suffix = m.group(1).upper()
        raw = raw[: m.start()].strip()
    if not _AMOUNT_RE.match(raw):
        raise ParseError(f"unparseable amount {text!r}")
    negative = (raw.startswith("(") and raw.endswith(")")) or trailing_minus
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
