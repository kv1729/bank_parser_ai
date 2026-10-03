"""
A library of layout-independent header rules and bank identities.

Used when no template matches (Workflow 1) and by the template agent as a
starting point. These are deliberately conservative: a field is better left
empty than filled with the wrong value (validation cannot check names).
"""
import re

from bank_parser.templates.model import HeaderRule

_DATE = r"\d{1,2}[-/ .](?:\d{1,2}|[A-Za-z]{3,9})[-/ .]\d{2,4}"
_MONEY = r"-?[\d,]+\.\d{2}(?:\s?(?:CR|DR|Cr|Dr))?"

GENERIC_HEADER_RULES = (
    HeaderRule("regex", ("header.account_number",), (1, 2),
               r"(?:Account|A/c|Acct)\.?\s*(?:Number|No)\.?\s*:?\s*([0-9X*]{6,20})\b", "i"),
    HeaderRule("regex", ("header.ifsc",), (1, 2),
               r"(?i:\bIFS(?:C)?(?:\s*Code)?)\s*:?\s*([A-Z]{4}0[A-Z0-9]{6})\b"),
    HeaderRule("regex", ("header.period_start", "header.period_end"), (1, 2),
               rf"(?:Statement\s+)?(?:from|period)\s*:?\s*({_DATE})\s*(?:to|-)\s*:?\s*({_DATE})", "i"),
    HeaderRule("regex", ("header.account_holder_name",), (1, 2),
               r"(?:Account\s+Name|Customer\s+Name)\s*:\s*(.+?)\s*$", "im"),
    HeaderRule("regex", ("header.account_holder_name",), (1,),
               r"^((?:Mr|Mrs|Ms|Miss|Dr|M/s)\.?[ \t]+[A-Za-z][A-Za-z.' \t]*?)(?=[ \t]+(?:[A-Za-z]+[ \t]+){0,2}[A-Za-z]+[ \t]*:|[ \t]*$)", "im"),
    HeaderRule("regex", ("header.account_holder_address",), (1, 2),
               r"(?:My|Customer|Communication|Mailing)\s+Address\s*:\s*(.+?)\s*$", "im"),
    HeaderRule("regex", ("header.account_holder_address",), (1,),
               r"Account\s+Name\s*:.*\nAddress\s*:\s*(.+?)\n(?:Date|Account\s+Number)\s*:", "is"),
    HeaderRule("regex", ("header.branch",), (1, 2),
               r"(?:Branch[ \t]+Name|Home[ \t]+Branch|Account[ \t]+Branch|Branch)[ \t]*:[ \t]*([A-Z][A-Za-z0-9 .&-]*?)[ \t]*(?=[ \t][A-Z][A-Za-z.]*(?:[ \t][A-Za-z.]+)*[ \t]*:|$)", "m"),
    HeaderRule("regex", ("header.currency",), (1, 2), r"\bCurrency\s*:\s*([A-Z]{3})\b"),
    HeaderRule("regex", ("header.opening_balance",), (1, 2),
               rf"(?:Opening\s+Balance|Balance\s+as\s+on[^:\n]*|Brought\s+Forward)\s*:?\s*({_MONEY})", "i"),
    # "Statement Summary" table on the last page (current SBI layout and similar)
    HeaderRule("regex", ("header.opening_balance", "summary.debit_count", "summary.credit_count",
                         "summary.total_debits", "summary.total_credits", "header.closing_balance"), (-1,),
               rf"Brought\s+Forward.*?Closing\s+Balance[^\n]*\n\s*({_MONEY})\s+(\d+)\s+(\d+)\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})",
               "is"),
    HeaderRule("regex", ("header.closing_balance",), (-1, 1),
               rf"Closing\s+Balance\s*:?\s*({_MONEY})", "i"),
)

# IFSC bank prefix -> (bank_code, bank name). Identity comes from the account's
# own IFSC (a labelled header field), never from IFSCs inside descriptions.
IFSC_BANKS = {
    "SBIN": ("sbi", "State Bank of India"), "HDFC": ("hdfc", "HDFC Bank"),
    "ICIC": ("icici", "ICICI Bank"), "UTIB": ("axis", "Axis Bank"),
    "PUNB": ("pnb", "Punjab National Bank"), "KKBK": ("kotak", "Kotak Mahindra Bank"),
    "BARB": ("bob", "Bank of Baroda"), "CNRB": ("canara", "Canara Bank"),
    "UBIN": ("union", "Union Bank of India"), "IDIB": ("indianbank", "Indian Bank"),
    "YESB": ("yes", "Yes Bank"), "IDFB": ("idfcfirst", "IDFC FIRST Bank"),
    "INDB": ("indusind", "IndusInd Bank"), "BKID": ("boi", "Bank of India"),
}

_NAME_KEYWORDS = tuple((re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I), code, name)
                       for code, name in {v[0]: v[1] for v in IFSC_BANKS.values()}.items())


def identify_bank(ifsc, header_text=""):
    """Return (bank_code, bank_name) or ("unknown", None)."""
    if ifsc and ifsc[:4] in IFSC_BANKS:
        return IFSC_BANKS[ifsc[:4]]
    for rx, code, name in _NAME_KEYWORDS:
        if rx.search(header_text):
            return code, name
    return "unknown", None
