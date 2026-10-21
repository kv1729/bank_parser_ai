"""
Stage 10.8 (D-028): the template agent, as the app runs it, on every statement whose correct
output is known, each one held out (as if its layout were new).

Held out: an empty template registry in a temporary folder, so a case's own template is
never available; approved-heading examples come only from the *other* cases (as in 10.7).
No golden gate here: with an empty registry every golden statement would lose its template
and the gate would refuse everything. Nothing in data/ or the template registry changes
(run scripts/regress.py afterwards).

  A  the app's default proposers: heuristic, approved headings, local model
  B  the local model alone (Stage 10 exit test: "the agent alone")

Per case: status, correct (transactions identical to the known output; opening/closing
balance and period equal), wrongly accepted, seconds, candidates tried, refinements, the
proposer that won, model calls / retries / failures / tokens.

Prints statuses, booleans and numbers only; never statement text.
Report: data/exports/agent-eval/report.json, progress in data/exports/agent-eval/progress.log

    uv run python experiments/agent_eval.py [--config A,A,B] [--model qwen3.5:9b] [--no-goldens]
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_choice import fixture_case, renamed_headings  # noqa: E402

from bank_parser.config import Settings  # noqa: E402
from bank_parser.core.schema import statement_to_dict  # noqa: E402
from bank_parser.extraction.detection import detection_text  # noqa: E402
from bank_parser.extraction.engine import extract_with_template  # noqa: E402
from bank_parser.extraction.select import extract_known  # noqa: E402
from bank_parser.golden import password_candidates, reopen, snapshot  # noqa: E402
from bank_parser.learning import LearningBudget, TemplateAgent  # noqa: E402
from bank_parser.learning.examples import ExamplesProposer, table_example  # noqa: E402
from bank_parser.learning.proposers import HeuristicProposer, LLMProposer  # noqa: E402
from bank_parser.llm.ollama import OllamaClient  # noqa: E402
from bank_parser.pdf import open_pdf  # noqa: E402
from bank_parser.pdf.json_layout import load_layout  # noqa: E402
from bank_parser.pipeline import build_registry  # noqa: E402
from bank_parser.storage import Database  # noqa: E402
from bank_parser.templates import TemplateRegistry  # noqa: E402

US = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "us_layouts"
CORE = ("txn_date", "value_date", "description", "reference", "debit", "credit", "balance")
US_KEYS = ("txn_date", "description", "debit", "credit", "balance")
HEADER = ("opening_balance", "closing_balance", "period_start", "period_end")


class NoCells:
    """Layout copies keep word positions but not ruled lines: the heuristic takes its unruled path."""

    def find_table_cells(self, n):
        return []


class Case:
    def __init__(self, name, kind, opener, truth, keys=CORE, template=None):
        self.name, self.kind, self.opener, self.truth, self.keys, self.template = name, kind, opener, truth, keys, template
        self.example = None


def _pdf_opener(path, password, backend):
    def open_():
        class Both:
            def __enter__(self):
                self.a = open_pdf(path, password, backend)
                self.b = open_pdf(path, password, "pdfplumber")
                return self.a.__enter__(), self.b.__enter__()

            def __exit__(self, *exc):
                self.b.__exit__(*exc)
                self.a.__exit__(*exc)
        return Both()
    return open_


def _layout_opener(doc):
    def open_():
        class Same:
            def __enter__(self):
                return doc, NoCells()

            def __exit__(self, *exc):
                return False
        return Same()
    return open_


def cases(settings, with_goldens):
    registry = {t.template_id: t for t in build_registry(settings).all(include_unapproved=True)}
    if with_goldens and settings.db_path.exists():
        for g in Database(settings.db_path).active_goldens():
            path = settings.documents_dir / f"{g['document_id']}.pdf"
            _, password = reopen(path, password_candidates(settings))
            yield Case(f"golden-{g['document_id'][:8]}", "golden", _pdf_opener(path, password, settings.pdf_backend),
                       g["snapshot"], template=registry.get(g["template_id"]))
    for name in ("boi-23f4f42a", "icici-ec27b806", "sbi-d2e542a8"):
        doc, t, out = fixture_case(name)
        yield Case(f"copy-{name}", "copy", _layout_opener(doc), out, template=t)
        if name != "sbi-d2e542a8":
            hdoc, ht = renamed_headings(doc, t)
            yield Case(f"renamed-{name}", "renamed", _layout_opener(hdoc), out, template=ht)
    templates = build_registry(settings).all()
    for path in sorted(settings.synthetic_samples_dir.glob("*.pdf")):
        with open_pdf(path, None, settings.pdf_backend) as doc:
            best = extract_known(doc, detection_text(doc), templates)
        if best is not None:
            yield Case(f"synthetic-{path.stem}", "synthetic", _pdf_opener(path, None, settings.pdf_backend),
                       snapshot(statement_to_dict(best[1].statement)), template=best[0])
    for name in ("us-columns", "us-signed"):
        _, doc = load_layout(US / name / "layout.json")
        truth = json.loads((US / name / "truth.json").read_text(encoding="utf-8"))
        st = {"transactions": truth["transactions"],
              "header": {"opening_balance": truth["opening_balance"], "closing_balance": truth["closing_balance"],
                         "period_start": truth["period_start"], "period_end": truth["period_end"]}}
        yield Case(f"us-{name[3:]}", "us", _layout_opener(doc), st, US_KEYS)


def same_output(got, truth, keys):
    rows = [[str(t.get(k)) if t.get(k) is not None else None for k in keys] for t in got["transactions"]]
    want = [[str(t.get(k)) if t.get(k) is not None else None for k in keys] for t in truth["transactions"]]
    head = all(str(got["header"].get(k)) == str((truth.get("header") or {}).get(k)) for k in HEADER)
    return rows == want, head


def run_case(case, config, examples, settings, client, model):
    budget = LearningBudget(max_candidates=settings.learning_max_candidates,
                            max_refinements=settings.learning_max_refinements,
                            max_seconds=settings.learning_max_seconds,
                            max_llm_tokens=settings.llm_max_tokens or 4000)
    llm = LLMProposer(client, model)
    proposers = [llm] if config == "B" else [HeuristicProposer(), ExamplesProposer(examples), llm]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        registry = TemplateRegistry([], Path(tmp))
        agent = TemplateAgent(registry, budget=budget, backend=settings.pdf_backend, proposers=proposers)
        with case.opener() as (doc, cells):
            out = agent.learn_document(doc, cells)
            rows_ok = head_ok = False
            if out.template_id:
                got = snapshot(statement_to_dict(extract_with_template(doc, registry.get(out.template_id)).statement))
                rows_ok, head_ok = same_output(got, case.truth, case.keys)
    winner = next((a["proposer"] for a in out.attempts if a["accepted"]), None)
    accepted = out.template_id is not None
    return {"case": case.name, "kind": case.kind, "config": config, "status": out.status.value,
            "correct": accepted and rows_ok, "header_same": head_ok if accepted else None,
            "wrongly_accepted": accepted and not rows_ok, "seconds": round(out.duration_ms / 1000, 1),
            "candidates": len(out.attempts), "refinements": max(0, len(out.attempts) - 1), "winner": winner,
            "llm_calls": out.model.get("calls", 0), "llm_retries": out.model.get("retries", 0),
            "llm_failures": out.model.get("failures", 0), "llm_tokens": out.model.get("tokens", 0),
            "reason": "" if accepted else _masked_reason(out.reason)}


def _masked_reason(reason):
    """Reasons are made by code (check codes, counts, field names); cut to a short length anyway."""
    return (reason or "")[:400]


def summarise(rows):
    out = {}
    for config in sorted({r["config"] for r in rows}):
        rs = [r for r in rows if r["config"] == config]
        runs = sorted({r["run"] for r in rs})
        secs = [r["seconds"] for r in rs]
        out[config] = {
            "runs": len(runs),
            "correct": f"{sum(r['correct'] for r in rs)}/{len(rs)}",
            "wrongly_accepted": sum(r["wrongly_accepted"] for r in rs),
            "header_differs_when_correct": sum(1 for r in rs if r["correct"] and not r["header_same"]),
            "median_s": statistics.median(secs) if secs else None, "max_s": max(secs, default=None),
            "refinements": sum(r["refinements"] for r in rs),
            "model_calls": sum(r["llm_calls"] for r in rs), "model_retries": sum(r["llm_retries"] for r in rs),
            "model_failures": sum(r["llm_failures"] for r in rs), "model_tokens": sum(r["llm_tokens"] for r in rs),
            "won_by": {w: sum(1 for r in rs if r["winner"] == w) for w in sorted({r["winner"] or "-" for r in rs})},
        }
        if len(runs) > 1:
            key = lambda r: (r["case"], r["status"], r["correct"], r["winner"], r["candidates"])  # noqa: E731
            per = [sorted(key(r) for r in rs if r["run"] == n) for n in runs]
            out[config]["runs_identical"] = all(p == per[0] for p in per)
    return out


def main(configs, model, with_goldens, only="", report="report"):
    settings = Settings.from_env()
    client = OllamaClient(settings.llm_url, timeout=600)
    if not client.has_model(model):
        sys.exit(f"model {model} is not installed")
    all_cases = list(cases(settings, with_goldens))
    chosen = [c for c in all_cases if only in c.name]
    for c in all_cases:
        if c.template is not None:
            with c.opener() as (doc, _):
                c.example = table_example(doc, c.template)
    out_dir = settings.data_dir / "exports" / "agent-eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = (out_dir / f"{report}.log").open("w", encoding="utf-8")

    def progress(text):
        print(text, flush=True)
        log.write(text + "\n")
        log.flush()

    progress(f"{len(all_cases)} cases; configs {','.join(configs)}; model {model}")
    rows, run_no = [], {}
    for config in configs:
        run_no[config] = run_no.get(config, 0) + 1
        for i, case in enumerate(all_cases):
            if case not in chosen:
                continue
            examples = [{"payload": c.example} for j, c in enumerate(all_cases) if j != i and c.example]
            r = dict(run_case(case, config, examples, settings, client, model), run=run_no[config])
            rows.append(r)
            progress(f"{config}{r['run']} {case.name:36} {r['status']:12} correct {str(r['correct']):5} "
                     f"by {r['winner'] or '-':9} {r['seconds']:6}s cand {r['candidates']} model {r['llm_calls']} "
                     f"retry {r['llm_retries']} fail {r['llm_failures']}  {r['reason']}")
    summary = summarise(rows)
    progress("done: " + json.dumps(summary))
    log.close()
    (out_dir / f"{report}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="A,A,B")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--no-goldens", action="store_true")
    ap.add_argument("--only", default="", help="only cases whose name contains this (examples still from all others)")
    ap.add_argument("--report", default="report", help="report name (report.json / report.log)")
    args = ap.parse_args()
    main([c.strip() for c in args.config.split(",") if c.strip()], args.model, not args.no_goldens, args.only, args.report)
