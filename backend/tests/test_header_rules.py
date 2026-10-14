"""Header rules on synthetic word layouts (D-021): labelled blocks, month-name periods, currency."""
from datetime import date

from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES
from bank_parser.extraction.header import apply_header_rules
from bank_parser.pdf.backend import PageLayout, Word
from bank_parser.templates.model import HeaderRule

CW = 10.0          # character width of the fake font; line height 17, line pitch 23


def line(top, *cells):
    """cells: (x0, "words printed from x0 with single spaces")."""
    words = []
    for x0, text in cells:
        x = x0
        for t in text.split():
            words.append(Word(t, x, x + len(t) * CW, top, top + 17))
            x += (len(t) + 1) * CW
    return words


def page(*lines):
    return {1: PageLayout(1, 1500, 2000, tuple(w for l in lines for w in l))}


# Two columns of labels; the address value is printed beside its label and wraps
# one line above and one line below it (vertically centred), as on some bank layouts.
BLOCK_PAGE = page(
    line(380, (1285, "Date: 01/02/2026")),
    line(433, (1103, "12 MAIN ROAD")),
    line(456, (122, "Account holder name:"), (448, "RAVI KUMAR"), (776, "Account holder address:"), (1103, "NEAR CITY PARK")),
    line(479, (1103, "560001")),
    line(515, (122, "Customer ID:"), (448, "123"), (776, "IFSC:"), (1103, "BKID0001234")),
)


def run(rules, pages, count=1):
    values, problems = apply_header_rules(rules, pages, count, ("%d-%m-%Y",))
    return values, problems


def test_block_reads_value_up_to_next_column():
    values, _ = run((HeaderRule("block", ("header.account_holder_name",), (1,), r"Account\s+holder\s+name\s*:", "i"),),
                    BLOCK_PAGE)
    assert values == {"header.account_holder_name": "RAVI KUMAR"}


def test_block_takes_aligned_lines_above_and_below_but_not_other_fields():
    values, _ = run((HeaderRule("block", ("header.account_holder_address",), (1,), r"Account\s+holder\s+address\s*:", "i"),),
                    BLOCK_PAGE)
    # The date line (other x) and the IFSC line (label column occupied) are not part of the address.
    assert values == {"header.account_holder_address": "12 MAIN ROAD, NEAR CITY PARK, 560001"}


def test_block_without_value_on_label_line_yields_nothing():
    pages = page(line(100, (122, "Account holder address:")), line(123, (448, "SOMEWHERE")))
    values, problems = run((HeaderRule("block", ("header.account_holder_address",), (1,), r"address\s*:", "i"),), pages)
    assert values == {} and problems == []


def test_generic_rules_fill_labelled_blocks_and_rupee_currency():
    pages = page(*[l for l in (
        line(380, (1285, "Date: 01/02/2026")),
        line(433, (1103, "12 MAIN ROAD")),
        line(456, (122, "Account holder name:"), (448, "RAVI KUMAR"), (776, "Account holder address:"), (1103, "NEAR CITY PARK")),
        line(479, (1103, "560001")),
        line(900, (132, "1 01-02-2026 UPI/PAYMENT"), (826, "₹ 10.00")),
    )])
    values, _ = run(GENERIC_HEADER_RULES, pages)
    assert values["header.account_holder_name"] == "RAVI KUMAR"
    assert values["header.account_holder_address"] == "12 MAIN ROAD, NEAR CITY PARK, 560001"
    assert values["header.currency"] == "INR"


def test_generic_rules_month_name_period_and_currency_word():
    pages = page(line(97, (46, "Statement of Transactions in Savings Account no. 000123456789 in INR for the period "
                               "Jan 1, 2026 - Jan 31, 2026")),
                 line(128, (400, "Your Base Branch: SOME BANK CITY,")))
    values, _ = run(GENERIC_HEADER_RULES, pages)
    assert values["header.period_start"] == date(2026, 1, 1) and values["header.period_end"] == date(2026, 1, 31)
    assert values["header.currency"] == "INR"
    assert values["header.account_number"] == "000123456789"
    assert values["header.branch"] == "SOME BANK CITY"          # trailing comma dropped


def test_earlier_rules_still_win():
    """Fallbacks never replace a value an earlier rule found (layouts that worked are unchanged)."""
    pages = page(line(100, (20, "Branch : MAINSTREET Currency : USD")), line(130, (20, "Total ₹ 5.00")))
    values, _ = run(GENERIC_HEADER_RULES, pages)
    # The block fallback would read "MAINSTREET Currency : USD"; the ₹ fallback would say INR.
    assert values["header.branch"] == "MAINSTREET" and values["header.currency"] == "USD"
