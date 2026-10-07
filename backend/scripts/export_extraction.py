"""
Export stored extraction output to CSV (opens in Excel) without using the UI.

    cd backend
    uv run python scripts/export_extraction.py --list                 # documents and their extractions
    uv run python scripts/export_extraction.py --doc a9941c06         # latest extraction of that document
    uv run python scripts/export_extraction.py --doc a9941c06 --extraction 5   # a specific (older) one
    uv run python scripts/export_extraction.py --doc a9941c06 --json  # also the full stored JSON

Results live inside the SQLite database `data/bank_parser.db` (table
`extractions`, column `statement_json`); this script turns one of them into:

    data/exports/<doc>_extraction_<n>_transactions.csv   one row per transaction
    data/exports/<doc>_extraction_<n>_header.csv         account/statement fields, totals, validation

`data/` is gitignored, and the database is opened read-only, so this is safe
while the app is running. The files contain statement data: keep them local.
"""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from bank_parser.config import Settings

TXN_COLUMNS = ["row_index", "page", "txn_date", "value_date", "description", "reference",
               "debit", "credit", "amount", "direction", "balance", "extra"]


def connect(db_path):
    if not Path(db_path).exists():
        sys.exit(f"No database at {db_path} - nothing has been processed yet.")
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def list_documents(db):
    rows = db.execute("""SELECT d.id, d.original_name, d.status, d.page_count,
                                GROUP_CONCAT(e.id, ', ')
                         FROM documents d LEFT JOIN extractions e ON e.document_id = d.id
                         GROUP BY d.id ORDER BY d.created_at""").fetchall()
    print(f"{'doc':12s}  {'status':13s} {'pages':>5s}  extractions (latest last)  file")
    for doc_id, name, status, pages, exs in rows:
        print(f"{doc_id[:12]}  {status:13s} {pages or '-':>5}  {exs or '-':26s} {name}")


def export(db, doc_prefix, extraction_id, out_dir, write_json):
    docs = db.execute("SELECT id, original_name FROM documents WHERE id LIKE ?", (doc_prefix + "%",)).fetchall()
    if len(docs) != 1:
        sys.exit(f"'{doc_prefix}' matches {len(docs)} documents; use --list and give a longer prefix.")
    doc_id, name = docs[0]
    query = "SELECT id, created_at, workflow, template_id, report_json, statement_json FROM extractions WHERE document_id = ?"
    params = [doc_id]
    if extraction_id is not None:
        query += " AND id = ?"
        params.append(extraction_id)
    row = db.execute(query + " ORDER BY id DESC LIMIT 1", params).fetchone()
    if not row or not row[5]:
        sys.exit("No such extraction for this document (see --list).")
    ex_id, created, workflow, template_id, report_json, statement_json = row
    statement, report = json.loads(statement_json), json.loads(report_json)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{doc_id[:12]}_extraction_{ex_id}"
    with open(f"{stem}_transactions.csv", "w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel reads UTF-8
        w = csv.DictWriter(f, fieldnames=TXN_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for t in statement["transactions"]:
            w.writerow({**t, "extra": json.dumps(t.get("extra") or {}, ensure_ascii=False) if t.get("extra") else ""})
    with open(f"{stem}_header.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["field", "value"])
        for k, v in [("file", name), ("document_id", doc_id), ("extraction", ex_id), ("extracted_at", created),
                     ("path", workflow), ("template", template_id or "")]:
            w.writerow([k, v])
        for k, v in (statement.get("header") or {}).items():
            w.writerow([f"header.{k}", json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else ("" if v is None else v)])
        for k, v in (statement.get("summary") or {}).items():
            w.writerow([f"summary.{k}", "" if v is None else v])
        w.writerow(["validation.status", report.get("status")])
        for c in report.get("checks", []):
            w.writerow([f"validation.{c['check']}", f"{c['status']} ({len(c['issues'])} issues)"])
    written = [f"{stem}_transactions.csv", f"{stem}_header.csv"]
    if write_json:
        Path(f"{stem}.json").write_text(json.dumps(statement, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(f"{stem}.json")
    print(f"Extraction #{ex_id} of {name} ({created}, {len(statement['transactions'])} transactions):")
    for p in written:
        print("  " + str(Path(p).resolve()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list documents and their extractions")
    ap.add_argument("--doc", help="document id or its first characters (from --list)")
    ap.add_argument("--extraction", type=int, help="extraction number (default: the latest)")
    ap.add_argument("--json", action="store_true", help="also write the full stored JSON")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    db = connect(settings.db_path)
    if args.list or not args.doc:
        list_documents(db)
        if not args.doc:
            return
    export(db, args.doc, args.extraction, settings.data_dir / "exports", args.json)


if __name__ == "__main__":
    main()
