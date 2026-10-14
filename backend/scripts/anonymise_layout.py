"""
Make an anonymised copy of a golden statement's layout for CI (Stage 9, D-022).

    cd backend
    uv run python scripts/anonymise_layout.py --doc dd6efc05 --name boi-23f4f42a

Writes  tests/fixtures/layouts/<name>/layout.json    every word, at its exact position
        tests/fixtures/layouts/<name>/template.json  the layout's template (no provenance ids)
        tests/fixtures/layouts/<name>/output.json    what the engine extracts from the copy
and a preview of each page as PNG in data/exports/layout-preview/<name>/ (local only),
so the owner can check the copy before it is committed.

What is kept and what is replaced:
- kept: positions of every word; label vocabulary (column headings, field labels, the
  template's markers, month names, transaction-type codes); numbers of up to 3 digits
  (serial numbers, days, counts, page numbers);
- replaced: every other word, by a fake of the same shape (letters -> random letters of
  the same case, digits -> random digits), consistently (the same word -> the same fake);
  IFSC keeps its 4-letter bank code;
- regenerated: every amount. Debits and credits get random values; balances, the
  opening/closing balance and printed totals are recomputed so the balance chain and
  reconciliation still hold;
- shifted: every year by -8 (leap years stay leap years; order and periods unchanged).

The copy is refused (nothing written to tests/) if any real word of 3+ characters that
is not label vocabulary survives, or if the engine's output on the copy does not have
the same structure and validation status as the golden output. Only counts are printed.
"""
import argparse
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
import random
import re
import sys
from pathlib import Path

from bank_parser.config import Settings
from bank_parser.core.schema import statement_to_dict
from bank_parser.core.validation import chronological, validate_statement
from bank_parser.extraction import table as table_mod
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.engine import extract_with_template
from bank_parser.extraction.generic_rules import IFSC_BANKS
from bank_parser.extraction.parsing import ParseError, parse_amount
from bank_parser.extraction.select import extract_known
from bank_parser.golden import password_candidates, reopen, snapshot
from bank_parser.pdf import open_pdf
from bank_parser.pdf.backend import PageLayout, Word
from bank_parser.pdf.json_layout import LAYOUT_FORMAT, JsonLayoutDocument, load_layout, pages_to_dict
from bank_parser.storage import Database
from bank_parser.templates import TemplateRegistry, template_to_dict

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "layouts"
YEAR_SHIFT = -8

VOCAB = set("""
account acct a/c ac no no. number name holder customer cust id address branch ifsc ifs code micr cif
statement statements period from to date dated opening closing balance balances currency inr type savings current
mobile phone email e-mail nominee joint status product scheme open opened of for the and on as in at by bank
details summary total debit credit debits credits withdrawal withdrawals deposit deposits count brought forward
carried amount cheque chq transaction transactions remarks sr s serial page relationship manager ckyc
interest rate od limit drawing power available clear cleared uncleared hold lien sanctioned ref reference value
txn tran narration description particulars generated computer signature required this is mode mr mrs ms m/s
dr cr shri smt nomination registered city state pin country gstin detailed with without any you your our we
please note not does do here there all are be been an or if it its valid only are
upi imps neft rtgs tfr wdl dep atm pos ach nach ecs int cash transfer charges chrg chgs gst tax fee fees
jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september
october november december mon tue wed thu fri sat sun
""".split())
for _, name in IFSC_BANKS.values():
    VOCAB.update(w.lower() for w in name.split())

DATE_RE = re.compile(r"\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3,9})[-/.]\d{2,4}")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
SMALL_INT_RE = re.compile(r"\d{1,3}[.,:;)]?")
IFSC_RE = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
NUM_RE = re.compile(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(\D*)$")


def core(text):
    return text.strip(".:,;()[]")


def shift_years(text):
    def year(m):
        return str(int(m.group(1)) + YEAR_SHIFT)
    out = YEAR_RE.sub(year, text)
    m = re.fullmatch(r"(\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3,9})[-/.])(\d{2})", out)
    if m:
        out = m.group(1) + f"{(int(m.group(2)) + YEAR_SHIFT) % 100:02d}"
    return out


def format_like(original, value, indian, grouped):
    """Print `value` the way `original` is printed (prefix/suffix, grouping, decimals)."""
    m = NUM_RE.match(original)
    if not m:
        raise SystemExit("refused: an amount has an unexpected print format")
    prefix, num, suffix = m.groups()
    decimals = len(num.split(".")[1]) if "." in num else 0
    q = abs(value).quantize(Decimal(1).scaleb(-decimals))
    whole, _, frac = f"{q:f}".partition(".")
    if ("," in num or grouped) and len(whole) > 3:
        if indian:                                   # 1,23,45,678
            head, groups = whole[:-3], [whole[-3:]]
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            whole = ",".join(([head] if head else []) + groups)
        else:                                        # 12,345,678
            whole = f"{int(whole):,}"
    return prefix + whole + (("." + frac) if decimals else "") + suffix


class Faker:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.cache = {}

    def token(self, text):
        if text in self.cache:
            return self.cache[text]
        m = EMAIL_RE.search(text)
        if m:                                  # fake e-mail addresses use the reserved example.com domain
            local = "".join(self.rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(max(4, len(m.group(1)))))
            out = text[:m.start()] + f"{local}@example.com" + text[m.end():]
            self.cache[text] = out
            return out
        for _ in range(20):
            if IFSC_RE.fullmatch(text):
                out = text[:5] + "".join(self.rng.choice("0123456789") for _ in text[5:])
            else:
                out = "".join(self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if c.isupper() else
                              self.rng.choice("abcdefghijklmnopqrstuvwxyz") if c.islower() else
                              self.rng.choice("0123456789") if c.isdigit() else
                              self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if c.isalpha() else c for c in text)
            if out != text and core(out).lower() not in VOCAB:
                break
        self.cache[text] = out
        return out

    def money(self, lo=100, hi=2_500_000):
        return Decimal(self.rng.randint(lo, hi)) / 100


@contextmanager
def tag_rows():
    """Record which words the table engine puts into which row and column."""
    real_assign, real_add = table_mod._assign, table_mod._add_cells
    words_of_cells, rows = {}, {}

    def assign(line, columns):
        cells = real_assign(line, columns)
        if cells is not None:
            by_col = {}
            for w in line.words:
                col = next((c for c in columns if c.x0 <= w.x_mid < c.x1), None)
                by_col.setdefault(col, []).append(w)
            words_of_cells[id(cells)] = by_col
        return cells

    def add(row, cells):
        for col in cells:
            rows.setdefault(id(row), []).append((col.role, words_of_cells[id(cells)].get(col, [])))
        real_add(row, cells)

    table_mod._assign, table_mod._add_cells = assign, add
    try:
        yield rows
    finally:
        table_mod._assign, table_mod._add_cells = real_assign, real_add


def template_vocabulary(template):
    """Label words the template relies on: markers, column labels, header-rule labels and anchors."""
    words = set()
    for text in list(template.markers) + [c.label or "" for c in template.table.columns]:
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{2,}", text))
    for r in template.header_rules:
        for pattern in (r.pattern, r.after, r.before, r.strip):
            if pattern:
                words.update(w.lower() for w in re.findall(r"(?<!\\)[A-Za-z]{2,}", pattern))
    return words


def anonymise(pages, template, golden_snapshot, seed):
    VOCAB.update(template_vocabulary(template))
    doc = JsonLayoutDocument(pages)
    with tag_rows() as tagged:
        real_rows, _ = table_mod.assemble_rows(doc.iter_pages(), template.table)
    real = extract_with_template(doc, template)
    if snapshot(statement_to_dict(real.statement)) != golden_snapshot:
        raise SystemExit("refused: the layout does not reproduce the golden output")
    txns = real.statement.transactions
    if len(real_rows) != len(txns):
        raise SystemExit("refused: some rows were skipped by the engine")
    fake = Faker(seed)
    indian = any(re.search(r"\d,\d\d,\d{3}", w.text) for p in pages for w in p.words)
    grouped = any(re.search(r"\d,\d{3}\.\d", w.text) for p in pages for w in p.words)

    # Amounts: random debits/credits, balances recomputed in date order.
    fd = {t.row_index: (Decimal(0) if t.debit == 0 else fake.money()) if t.debit is not None else None for t in txns}
    fc = {t.row_index: (Decimal(0) if t.credit == 0 else fake.money()) if t.credit is not None else None for t in txns}
    opening = fake.money(5_000_000, 20_000_000) + sum(v for v in fd.values() if v)
    fb, bal = {}, opening
    for t in chronological(txns):
        bal = bal - (fd[t.row_index] or 0) + (fc[t.row_index] or 0)
        fb[t.row_index] = bal if t.balance is not None else None
    closing = bal
    h, s = real.statement.header, real.statement.summary
    money_map = {}
    for real_v, fake_v in ((h.opening_balance, opening), (h.closing_balance, closing),
                           (s.total_debits if s else None, sum(v for v in fd.values() if v is not None)),
                           (s.total_credits if s else None, sum(v for v in fc.values() if v is not None))):
        if real_v is not None:
            if money_map.get(real_v, fake_v) != fake_v:
                raise SystemExit("refused: two printed totals share a value; cannot tell them apart")
            money_map[real_v] = fake_v

    replaced = {}                                         # id(word) -> new text
    for i, row in enumerate(real_rows):
        values = {"debit": fd[i], "credit": fc[i], "balance": fb[i]}
        for role, words in tagged.get(id(row), []):
            if role in values:
                numeric = [w for w in words if re.search(r"\d", w.text)]
                if values[role] is None or values[role] == 0:
                    continue                              # "-", "0.00": printed as before
                if len(numeric) != 1:
                    raise SystemExit("refused: an amount is split over several words")
                replaced[id(numeric[0])] = format_like(numeric[0].text, values[role], indian, grouped)
            elif role in ("txn_date", "value_date"):
                for w in words:
                    replaced[id(w)] = shift_years(w.text)

    counts = {str(v) for v in ((s.debit_count, s.credit_count) if s else ()) if v is not None}
    in_rows = {id(w) for row in real_rows for _, words in tagged.get(id(row), []) for w in words}
    months = {m for m in VOCAB if m[:3] in ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                              "nov", "dec")}

    def keep_small_int(w, prev, nxt):
        """Serial numbers, days next to a month name, page numbers and printed counts stay as printed."""
        if id(w) in in_rows or core(w.text) in counts:
            return True
        near = {core(x.text).lower() for x in (prev, nxt) if x is not None}
        return bool(near & (months | {"page", "of"}))

    def generic(text, w=None, prev=None, nxt=None):
        c = core(text)
        if not any(ch.isalnum() for ch in c) or c.lower() in VOCAB:
            return text
        try:
            v, _ = parse_amount(text, empty_tokens=())
            if v is not None and v in money_map and "." in text:
                return format_like(text, money_map[v], indian, grouped)
        except ParseError:
            pass
        if DATE_RE.fullmatch(c) or YEAR_RE.fullmatch(c):
            return shift_years(text)
        if SMALL_INT_RE.fullmatch(text) and (w is None or keep_small_int(w, prev, nxt)):
            return text
        return fake.token(text)

    def neighbours(p):
        ws = sorted(p.words, key=lambda w: (round(w.top), w.x0))
        return {id(w): (ws[i - 1] if i else None, ws[i + 1] if i + 1 < len(ws) else None) for i, w in enumerate(ws)}

    fake_pages = []
    for p in pages:
        nb = neighbours(p)
        fake_pages.append(PageLayout(p.number, p.width, p.height, tuple(
            Word(replaced.get(id(w)) or generic(w.text, w, *nb[id(w)]), w.x0, w.x1, w.top, w.bottom) for w in p.words)))
    return real, fake_pages, (fd, fc, fb)


def leaks(real_pages, fake_text, golden_snapshot):
    """Real words (3+ characters, not vocabulary or small numbers) that survive in the copy."""
    fake_tokens = set(re.findall(r"\S+", fake_text))
    out = set()
    for p in real_pages:
        for w in p.words:
            c = core(w.text)
            if len(c) >= 3 and c.lower() not in VOCAB and not SMALL_INT_RE.fullmatch(w.text) and \
                    (w.text in fake_tokens or c in fake_tokens):
                out.add(c)
    h = golden_snapshot["header"]
    for key in ("account_holder_name", "account_holder_address", "account_number", "branch", "bank_address"):
        for part in re.split(r"[\s,]+", h.get(key) or ""):
            if len(part) >= 4 and part.lower() not in VOCAB and part in fake_text:
                out.add(part)
    return out


def preview(pages, out_dir):
    """Draw each word at its position (2x for pages in points), sized to its box, for the owner to check."""
    from PIL import Image, ImageDraw, ImageFont
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        k = 2.0 if p.width < 1000 else 1.0
        img = Image.new("RGB", (int(p.width * k) + 1, int(p.height * k) + 1), "white")
        d = ImageDraw.Draw(img)
        for w in p.words:
            size = max(8, int((w.bottom - w.top) * k * 0.85))
            d.text((w.x0 * k, w.top * k), w.text, fill="black", font=ImageFont.load_default(size=size))
        img.save(out_dir / f"page-{p.number}.png")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Anonymised copy of a golden statement's layout")
    ap.add_argument("--doc", required=True, help="golden document id prefix")
    ap.add_argument("--name", required=True, help="fixture folder name, e.g. boi-23f4f42a")
    ap.add_argument("--seed", type=int, default=20261014)
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    db = Database(settings.db_path)
    goldens = [g for g in db.active_goldens() if g["document_id"].startswith(args.doc)]
    if len(goldens) != 1:
        sys.exit("refused: the prefix must name exactly one golden statement")
    g = goldens[0]
    if g["workflow"] != "known_template":
        sys.exit("refused: only statements extracted by a template can be copied")
    registry = TemplateRegistry([settings.committed_templates_dir], settings.learned_templates_dir)
    template = registry.get(g["template_id"])
    path = settings.documents_dir / f"{g['document_id']}.pdf"
    try:
        _, password = reopen(path, password_candidates(settings))
        with open_pdf(path, password, settings.pdf_backend) as pdf:
            pages = [pdf.page(n) for n in range(1, pdf.page_count + 1)]
        real, fake_pages, _ = anonymise(pages, template, g["snapshot"], args.seed)
    except SystemExit:
        raise
    except Exception as e:                     # never echo exception text: it may contain content
        sys.exit(f"refused: {type(e).__name__} while reading the statement")

    fixture_template = template_to_dict(template)
    fixture_template["provenance"] = {"created_by": "anonymised fixture", "copied_from": template.template_id}
    fixture_template["regression"] = {}
    layout = {"format": LAYOUT_FORMAT, "layout": f"{template.bank_code}/{template.layout_id}",
              "note": "Anonymised copy of a real statement's layout: positions kept, every value replaced (D-022).",
              "pages": pages_to_dict(fake_pages)}

    # Re-read exactly what will be committed and extract it, as CI will.
    scratch = settings.data_dir / "exports" / "layout-preview" / args.name
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "layout.json").write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    _, fake_doc = load_layout(scratch / "layout.json")
    best = extract_known(fake_doc, detection_text(fake_doc), [template])
    if best is None:
        sys.exit("refused: the template does not extract the anonymised copy")
    _, result, report = best
    out = snapshot(statement_to_dict(result.statement))
    gs = g["snapshot"]
    checks = {
        "same row count": len(out["transactions"]) == len(gs["transactions"]),
        "same filled header fields": {k for k, v in out["header"].items() if v not in (None, "", {})} ==
                                     {k for k, v in gs["header"].items() if v not in (None, "", {})},
        "same debit/credit/balance pattern": [(t["debit"] is None, t["credit"] is None, t["balance"] is None)
                                              for t in out["transactions"]] ==
                                             [(t["debit"] is None, t["credit"] is None, t["balance"] is None)
                                              for t in gs["transactions"]],
        "same validation status": report.status.value == g["validation_status"],
        "amounts changed": all(a["balance"] != b["balance"] for a, b in zip(out["transactions"], gs["transactions"])
                               if a["balance"] is not None),
    }
    text = json.dumps(layout, ensure_ascii=False) + json.dumps(out, ensure_ascii=False) + json.dumps(fixture_template)
    leaked = leaks(pages, text, gs)
    (scratch / "leak-check.txt").write_text("\n".join(sorted(leaked)), encoding="utf-8")   # local only
    preview(fake_pages, scratch)

    print(f"anonymise: {len(fake_pages)} pages, {sum(len(p.words) for p in fake_pages)} words, "
          f"{len(out['transactions'])} rows, validation {report.status.value}")
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    filled = lambda h: {k for k, v in h.items() if v not in (None, "", {})}
    if filled(out["header"]) != filled(gs["header"]):
        print(f"       header fields only in the golden output: {sorted(filled(gs['header']) - filled(out['header']))}; "
              f"only in the copy: {sorted(filled(out['header']) - filled(gs['header']))}")
    print(f"  {'ok  ' if not leaked else 'FAIL'} real words surviving: {len(leaked)}")
    print(f"  preview: {scratch}")
    if leaked or not all(checks.values()):
        sys.exit("refused: nothing written to tests/ (see the checks above)")

    dest = FIXTURES / args.name
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    (dest / "template.json").write_text(json.dumps(fixture_template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "output.json").write_text(json.dumps({"template_id": template.template_id,
                                                  "validation_status": report.status.value, "statement": out},
                                                 indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  wrote {dest.relative_to(FIXTURES.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
