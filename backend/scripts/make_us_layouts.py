"""
Write synthetic US statement layouts for tests (Stage 10.6, D-026). Fake data only.

There is no PDF writer in the project, so each statement is written as positioned words
(the format of pdf/json_layout.py), the same form the anonymised layout copies use.
Alongside each layout, `truth.json` holds what the script printed (transactions, balances,
period, routing number) so tests compare the parser against the source, not against
its own earlier output.

    uv run python scripts/make_us_layouts.py      # rewrites tests/fixtures/us_layouts/
"""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from bank_parser.pdf.json_layout import LAYOUT_FORMAT

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "us_layouts"
CHAR = 4.6             # average character width at 9 pt
HEIGHT = 9.0
ROUTING = "123456780"  # the usual example routing number; passes the ABA checksum


class Page:
    def __init__(self, number, width=612.0, height=792.0):
        self.number, self.width, self.height, self.words = number, width, height, []

    def text(self, x, top, text, right=False):
        """Write `text` word by word starting at x (or ending at x when right=True)."""
        if right:
            x -= CHAR * len(text)
        for w in text.split(" "):
            if w:
                self.words.append([w, round(x, 2), round(x + CHAR * len(w), 2), round(top, 2), round(top + HEIGHT, 2)])
            x += CHAR * (len(w) + 1)

    def to_dict(self):
        return {"number": self.number, "width": self.width, "height": self.height, "words": self.words}


def money(v):
    return f"{v:,.2f}"


def write(name, pages, truth):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "layout.json").write_text(json.dumps({"format": LAYOUT_FORMAT, "source": "synthetic US statement (fake data)",
                                                    "pages": [p.to_dict() for p in pages]}, indent=1), encoding="utf-8")
    (folder / "truth.json").write_text(json.dumps(truth, indent=2, default=str), encoding="utf-8")


def rows_with_balance(opening, items):
    out, bal = [], opening
    for d, desc, amount in items:
        bal += amount
        out.append((d, desc, amount, bal))
    return out


def header(p, period_line, opening, closing, deposits, withdrawals, holder):
    p.text(40, 40, "First Example Bank, N.A.")
    p.text(40, 56, "PO Box 0000, Springfield, ST 00000")
    p.text(40, 90, holder)
    p.text(40, 102, "100 Sample Street Apt 1")
    p.text(40, 114, "Springfield ST 00000")
    p.text(330, 90, "Account Number: XXXXXX4321")
    p.text(330, 102, f"Routing Number: {ROUTING}")
    p.text(330, 114, period_line)
    p.text(40, 150, "Account Summary")
    p.text(40, 164, f"Beginning Balance ${money(opening)}")
    p.text(40, 176, f"Total Deposits and Additions ${money(deposits)}")
    p.text(40, 188, f"Total Withdrawals and Subtractions ${money(withdrawals)}")
    p.text(40, 200, f"Ending Balance ${money(closing)}")


def us_columns():
    """Date | Description | Withdrawals | Deposits | Balance; year-less dates across a year end."""
    opening = Decimal("1250.00")
    items = [
        ("12/18", ["CARD PURCHASE GROCERY MART", "SPRINGFIELD ST"], Decimal("-82.17")),
        ("12/22", ["ACH DEPOSIT EXAMPLE PAYROLL"], Decimal("1520.00")),
        ("12/27", ["CHECK 1043"], Decimal("-400.00")),
        ("12/30", ["ONLINE TRANSFER TO SAVINGS", "REF 99812"], Decimal("-250.00")),
        ("01/02", ["CARD PURCHASE COFFEE HOUSE"], Decimal("-6.45")),
        ("01/05", ["ACH DEBIT UTILITY CO"], Decimal("-131.01")),
        ("01/09", ["ATM WITHDRAWAL MAIN ST"], Decimal("-60.00")),
        ("01/10", ["MOBILE DEPOSIT"], Decimal("75.25")),
        ("01/12", ["CARD PURCHASE BOOK STORE", "SPRINGFIELD ST"], Decimal("-24.99")),
        ("01/14", ["MONTHLY SERVICE FEE"], Decimal("-12.00")),
    ]
    rows = rows_with_balance(opening, items)
    closing = rows[-1][3]
    deposits = sum(a for _, _, a in items if a > 0)
    withdrawals = -sum(a for _, _, a in items if a < 0)
    p1, p2 = Page(1), Page(2)
    header(p1, "Statement Period: December 15, 2023 through January 14, 2024", opening, closing, deposits, withdrawals,
           "JANE Q SAMPLE")
    cols = {"date": 40, "desc": 90, "wd": 400, "dep": 480, "bal": 570}       # right edges for amounts

    def table(page, top, chunk):
        page.text(cols["date"], top, "Date")
        page.text(cols["desc"], top, "Description")
        page.text(cols["wd"] - CHAR * 11, top, "Withdrawals")
        page.text(cols["dep"] - CHAR * 8, top, "Deposits")
        page.text(cols["bal"] - CHAR * 7, top, "Balance")
        y = top + 18
        for d, desc, amount, bal in chunk:
            page.text(cols["date"], y, d)
            page.text(cols["desc"], y, desc[0])
            page.text(cols["wd"] if amount < 0 else cols["dep"], y, money(abs(amount)), right=True)
            page.text(cols["bal"], y, money(bal), right=True)
            for extra in desc[1:]:
                y += 11
                page.text(cols["desc"], y, extra)
            y += 16
        return y

    table(p1, 240, rows[:6])
    p1.text(40, 740, "Page 1 of 2")
    p2.text(40, 40, "First Example Bank, N.A.   Account Number: XXXXXX4321")
    table(p2, 80, rows[6:])
    p2.text(40, 740, "Page 2 of 2")
    year = {12: 2023, 1: 2024}
    truth = {"period_start": "2023-12-15", "period_end": "2024-01-14", "opening_balance": str(opening),
             "closing_balance": str(closing), "routing_number": ROUTING, "currency": "USD",
             "total_debits": str(withdrawals), "total_credits": str(deposits),
             "transactions": [{"txn_date": str(date(year[int(d[:2])], int(d[:2]), int(d[3:]))),
                               "description": " ".join(desc),
                               "debit": str(-a) if a < 0 else None, "credit": str(a) if a > 0 else None,
                               "balance": str(b)} for d, desc, a, b in rows]}
    write("us-columns", [p1, p2], truth)


def us_signed():
    """Posting Date | Description | Amount | Balance; one signed column, leading and trailing minus."""
    opening = Decimal("2000.00")
    items = [
        ("Jan 03, 2024", "DIRECT DEPOSIT EXAMPLE CORP", Decimal("2100.00")),
        ("Jan 05, 2024", "RENT PAYMENT ONLINE", Decimal("-1450.00")),
        ("Jan 11, 2024", "POS PURCHASE HARDWARE STORE", Decimal("-45.10")),
        ("Jan 16, 2024", "INTEREST PAYMENT", Decimal("0.42")),
        ("Jan 22, 2024", "ACH DEBIT CREDIT CARD AUTOPAY", Decimal("-310.55")),
        ("Jan 29, 2024", "POS PURCHASE PHARMACY", Decimal("-23.99")),
    ]
    rows = rows_with_balance(opening, items)
    closing = rows[-1][3]
    deposits = sum(a for _, _, a in items if a > 0)
    withdrawals = -sum(a for _, _, a in items if a < 0)
    p = Page(1)
    header(p, "Statement Period: 01/01/2024 - 01/31/2024", opening, closing, deposits, withdrawals, "JOHN R EXAMPLE")
    top = 240
    p.text(40, top, "Posting Date")
    p.text(120, top, "Description")
    p.text(470 - CHAR * 6, top, "Amount")
    p.text(570 - CHAR * 7, top, "Balance")
    y = top + 18
    for i, (d, desc, amount, bal) in enumerate(rows):
        p.text(40, y, d)
        p.text(120, y, desc)
        shown = money(amount) if amount >= 0 else (f"{money(-amount)}-" if i % 2 else f"-{money(-amount)}")
        p.text(470, y, shown, right=True)
        p.text(570, y, money(bal), right=True)
        y += 16
    p.text(40, 740, "Page 1 of 1")
    truth = {"period_start": "2024-01-01", "period_end": "2024-01-31", "opening_balance": str(opening),
             "closing_balance": str(closing), "routing_number": ROUTING, "currency": "USD",
             "total_debits": str(withdrawals), "total_credits": str(deposits),
             "transactions": [{"txn_date": str(date(2024, 1, int(d[4:6]))), "description": desc,
                               "debit": str(-a) if a < 0 else None, "credit": str(a) if a > 0 else None,
                               "balance": str(b)} for d, desc, a, b in rows]}
    write("us-signed", [p], truth)


if __name__ == "__main__":
    us_columns()
    us_signed()
    print(f"wrote {OUT}")
