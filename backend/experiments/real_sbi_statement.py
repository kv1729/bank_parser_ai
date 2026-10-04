"""Experiment (DECISIONS.md D-005): pdfplumber table extraction + bank_parser validation
on the real SBI statement. Run: cd backend && uv run python experiments/real_sbi_statement.py
Prints only masked/aggregate output."""
import re, sys, time, statistics
from datetime import datetime
from decimal import Decimal
import pdfplumber
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
from bank_parser import Statement, StatementHeader, Transaction, validate_balance_chain

P = REPO_ROOT / "sample_data/AccountStatement_02102026_120158.pdf"
PW = open(REPO_ROOT / "sample_data/.pdf_password", encoding="utf-8").read().strip()

def money(s):
    s = (s or "").strip()
    if s in ("", "-"): return None
    s = re.sub(r"\s*(CR|DR)$", "", s)          # balance suffix, if any
    return Decimal(s.replace(",", ""))

def run(debit_col=4, credit_col=5, verbose=False):
    t = {}
    t0 = time.perf_counter()
    pdf = pdfplumber.open(P, password=PW)
    t["open"] = time.perf_counter() - t0
    rows, fragments = [], 0
    t1 = time.perf_counter()
    with pdf:
        pages = pdf.pages
        for pn, pg in enumerate(pages[1:8], start=2):
            for tb in pg.extract_tables():
                for r in tb:
                    if len(r) != 7 or r[-1] == "Balance": continue
                    if not (r[0] or "").strip():
                        fragments += 1
                        if (r[2] or "").strip(): rows.append(("FRAG", pn, r))
                        continue
                    rows.append(("ROW", pn, r))
        summ = pages[8].extract_tables()[0][2]
    t["layout+tables"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    txns = []
    for kind, pn, r in rows:
        if kind == "FRAG":
            prev = txns[-1]
            txns[-1] = Transaction(**{**prev.__dict__, "description": prev.description + " " + " ".join((r[2] or "").split())})
            continue
        txns.append(Transaction(row_index=len(txns), page=pn,
            txn_date=datetime.strptime(r[0].strip(), "%d/%m/%Y").date(),
            value_date=datetime.strptime(r[1].strip(), "%d/%m/%Y").date(),
            description=" ".join((r[2] or "").split()), reference=(r[3] or "").strip() or None,
            debit=money(r[debit_col]), credit=money(r[credit_col]), balance=money(r[6])))
    opening = money(summ[0]); closing = money(summ[5])
    st = Statement(header=StatementHeader(bank_name="State Bank of India", currency="INR", opening_balance=opening, closing_balance=closing), transactions=tuple(txns))
    t["normalize"] = time.perf_counter() - t2
    t3 = time.perf_counter()
    res = validate_balance_chain(st)
    t["validate"] = time.perf_counter() - t3
    t["total"] = sum(t.values())
    # summary-page invariants (not yet in bank_parser)
    dr = [x.debit for x in txns if x.debit is not None]; cr = [x.credit for x in txns if x.credit is not None]
    summary = {
        "dr_count": (len(dr), int(summ[1])), "cr_count": (len(cr), int(summ[2])),
        "total_debits": (sum(dr), money(summ[3])), "total_credits": (sum(cr), money(summ[4])),
        "closing": (txns[-1].balance, closing),
    }
    return res, summary, t, len(txns), fragments, txns

if __name__ == "__main__":
    for cols in ((4, 5), (5, 4)):
        res, summary, t, n, frag, txns = run(*cols)
        print(f"debit=col{cols[0]} credit=col{cols[1]}: rows={n} continuation_fragments={frag} chain={res.status.value} rows_checked={res.rows_checked} issues={len(res.issues)} {[i.code for i in res.issues][:5]}")
    res, summary, t, n, frag, txns = run(4, 5)
    print("summary-page reconciliation (extracted == printed):")
    for k, (a, b) in summary.items(): print(f"   {k:14s} {'OK' if a == b else 'MISMATCH'}")
    print("date order non-decreasing:", all(txns[i].txn_date <= txns[i+1].txn_date for i in range(len(txns)-1)))
    print("exactly one of debit/credit per row:", all((x.debit is None) != (x.credit is None) for x in txns))
    print("rows with a reference:", sum(1 for x in txns if x.reference and x.reference != '-'), "of", n)
    runs = [run(4, 5)[2] for _ in range(10)]
    print("timing over 10 runs (median ms):")
    for k in runs[0]: print(f"   {k:14s} {statistics.median(r[k] for r in runs)*1000:8.2f}")
    print("   p95-ish total (max of 10):", round(max(r['total'] for r in runs)*1000, 1), "ms")
