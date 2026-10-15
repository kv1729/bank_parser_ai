"""
Stage 10.2 hold-out: the template agent with ONLY the local-model proposer and an empty
template registry learns each golden statement from scratch (as if its layout were
new); the learned template's transactions are compared with the approved golden output.

Prints statuses and booleans only; never statement content.

    uv run python experiments/llm_holdout.py [--model qwen3.5:9b]
"""
import argparse
import tempfile
from pathlib import Path

from bank_parser.config import Settings
from bank_parser.core.schema import statement_to_dict
from bank_parser.extraction.engine import extract_with_template
from bank_parser.golden import password_candidates, reopen, snapshot
from bank_parser.learning import LearningBudget, TemplateAgent
from bank_parser.learning.proposers import LLMProposer
from bank_parser.llm.ollama import OllamaClient
from bank_parser.pdf import open_pdf
from bank_parser.storage import Database
from bank_parser.templates import TemplateRegistry

CORE = ("date", "value_date", "description", "reference", "debit", "credit", "balance")


def core(statement):
    return [tuple(str(t.get(k)) for k in CORE) for t in statement["transactions"]]


def main(model):
    settings = Settings.from_env()
    db = Database(settings.db_path)
    for g in db.active_goldens():
        path = settings.documents_dir / f"{g['document_id']}.pdf"
        _, password = reopen(path, password_candidates(settings))
        with tempfile.TemporaryDirectory() as tmp:
            registry = TemplateRegistry([], Path(tmp))
            agent = TemplateAgent(registry, budget=LearningBudget(max_llm_tokens=settings.llm_max_tokens or 4000),
                                  backend=settings.pdf_backend,
                                  proposers=[LLMProposer(OllamaClient(timeout=600), model)])
            out = agent.learn(str(path), password, g["document_id"])
            same = None
            if out.template_id:
                t = registry.get(out.template_id)
                with open_pdf(path, password, settings.pdf_backend) as doc:
                    got = snapshot(statement_to_dict(extract_with_template(doc, t, None).statement))
                same = core(got) == core(g["snapshot"])
            print(f"golden-{g['document_id'][:8]}  {out.status.value:12}  attempts {len(out.attempts)}  "
                  f"tokens {out.attempts[-1]['llm_tokens'] if out.attempts else 0}  {out.duration_ms / 1000:.0f}s  "
                  f"transactions identical to golden: {same}  {'' if out.template_id else out.reason}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    main(ap.parse_args().model)
