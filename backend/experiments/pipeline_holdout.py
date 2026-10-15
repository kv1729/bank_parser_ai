"""
Stage 10.3 end to end: each golden statement goes through the real pipeline as if its
layout were new: a temporary data folder, no templates, and the template agent limited
to the local model ("llm" proposer only). Records the status after Workflow 1, the
final status, the time until the final result, and whether the final transactions equal
the approved golden ones.

Prints statuses and booleans only; never statement content. Nothing in data/ changes:
the PDFs are read and copied into the temporary folder.

    uv run python experiments/pipeline_holdout.py
"""
from dataclasses import replace
import tempfile
import time
from pathlib import Path

from bank_parser.config import Settings
from bank_parser.golden import password_candidates, reopen
from bank_parser.pipeline import Processor
from bank_parser.storage import Database

CORE = ("txn_date", "value_date", "description", "reference", "debit", "credit", "balance")


def core(statement):
    return [tuple(str(t.get(k)) for k in CORE) for t in statement["transactions"]]


def main():
    real = Settings.from_env()
    goldens = Database(real.db_path).active_goldens()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # SQLite may hold the file briefly
        settings = replace(real, data_dir=Path(tmp) / "data", committed_templates_dir=Path(tmp) / "none",
                           learning_proposers=("llm",), llm_max_tokens=real.llm_max_tokens or 4000)
        p = Processor(settings, learning_mode="thread")
        try:
            for g in goldens:
                path = real.documents_dir / f"{g['document_id']}.pdf"
                _, password = reopen(path, password_candidates(real))
                with open(path, "rb") as f:
                    doc_id = p.ingest(f.read, "statement.pdf").document_id
                t0 = time.perf_counter()
                first = p.process(doc_id, password)
                p.wait_idle(900)
                seconds = time.perf_counter() - t0
                final = p.db.get_document(doc_id)["status"]
                ex = p.db.latest_extraction(doc_id)
                job = p.db.latest_template_job(doc_id) or {}
                proposer = (job.get("attempts") or [{}])[-1].get("proposer")
                same = bool(ex and ex["statement"]) and core(ex["statement"]) == core(g["snapshot"])
                print(f"golden-{g['document_id'][:8]}  after Workflow 1: {first:12}  final: {final:12}  "
                      f"learned by: {proposer}  {seconds:.0f}s  workflow: {ex['workflow'] if ex else None}  "
                      f"transactions identical to golden: {same}", flush=True)
        finally:
            p.shutdown()


if __name__ == "__main__":
    main()
