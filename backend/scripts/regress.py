"""
Non-regression run (Stage 9, D-022): re-extract every golden statement with the
current code and templates, and compare it field by field with its approved snapshot.

    cd backend
    uv run python scripts/regress.py                  # gate: exit code 1 on any difference
    uv run python scripts/regress.py --backends both  # also pdfplumber (reported, not gated)
    uv run python scripts/regress.py --show-values    # print values instead of shape masks

Statements become golden in /review ("Approve as golden"). There is deliberately no
option to accept a difference here: a changed output is re-approved by a human in
/review, which adds a new golden version (the old one is kept).

Values are masked by default (letters -> a/A, digits -> 9) because this also runs
from the pre-push hook. A JSON report is written to data/regress/ (gitignored).
"""
import argparse
from datetime import datetime, timezone
import json
import sys

from bank_parser.config import Settings
from bank_parser.golden import run_regression, shape
from bank_parser.storage import Database
from bank_parser.templates import TemplateRegistry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--backends", choices=("default", "both"), default="default")
    ap.add_argument("--show-values", action="store_true")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if not settings.db_path.exists():
        print("regress: no local database, so no golden statements to check (skipped).")
        return 0
    db = Database(settings.db_path)
    goldens = db.active_goldens()
    if not goldens:
        print("regress: no golden statements yet (approve them in /review). Skipped.")
        return 0
    registry = TemplateRegistry([settings.committed_templates_dir], settings.learned_templates_dir)
    extra = ("pdfplumber",) if args.backends == "both" and settings.pdf_backend != "pdfplumber" else ()
    report = run_regression(db, settings, registry.all(), extra_backends=extra, goldens=goldens)

    fmt = (lambda v: v) if args.show_values else shape
    print(f"regress: {len(goldens)} golden statement(s), gate backend {report.backend}")
    for r in report.results:
        gate = "" if r.backend == report.backend else "  (informational)"
        moved = f"  template {r.template_then} -> {r.template_now}" if r.template_then != r.template_now else ""
        print(f"  {r.status:5s} {r.document_id[:12]} golden v{r.version} [{r.backend}] {r.ms:.0f} ms{moved}{gate}")
        if r.error:
            print(f"        error: {r.error}")
        for c in r.changes[:40]:
            print(f"        {c.path}: {fmt(c.old)} -> {fmt(c.new)}")
        if len(r.changes) > 40:
            print(f"        ... {len(r.changes) - 40} more")

    out_dir = settings.data_dir / "regress"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{stamp}.json").write_text(json.dumps(report.to_dict(), indent=1), encoding="utf-8")

    if report.ok:
        print("regress: PASS - every golden statement is unchanged.")
        return 0
    print(f"regress: FAIL - {len(report.failed)} golden statement(s) changed or could not be checked.")
    print("  If the change is intended, re-extract those statements in /review, check them and approve again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
