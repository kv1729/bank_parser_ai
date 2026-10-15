"""
Stage 10.1: choose the local model (D-023; PLAN §6: <=3 candidates, <=2 tested).

For every case the right answer is known but hidden from the model: the model
reads the page (learning/llm_view.py), code builds the table layout from its
answer, the statement is extracted with it, and the result is compared with
the known output. Only the *table* is scored (header rules come from the known
template), because that is the model's whole job in the proposer.

Cases:
  base  - the golden statements (local only), the anonymised BOI/ICICI copies,
          the synthetic PDFs;
  hard  - the anonymised copies with their heading words swapped for other
          banks' wording, and the SBI copy (its headings are fake words).

Prints scores only; never statement text. Report: data/exports/llm-choice/<model>.json

    uv run python experiments/llm_choice.py --model qwen3.5:4b
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import time

from bank_parser.config import Settings
from bank_parser.core.schema import statement_to_dict
from bank_parser.core.validation import validate_statement
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.engine import extract_with_template
from bank_parser.extraction.lines import group_lines
from bank_parser.extraction.select import extract_known
from bank_parser.golden import password_candidates, reopen, snapshot
from bank_parser.learning import llm_view
from bank_parser.llm.ollama import LLMError, OllamaClient
from bank_parser.pdf import open_pdf
from bank_parser.pdf.backend import PageLayout, Word
from bank_parser.pdf.json_layout import JsonLayoutDocument, load_layout
from bank_parser.pipeline import build_registry
from bank_parser.storage import Database
from bank_parser.templates import template_from_dict

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "layouts"
CORE = ("txn_date", "value_date", "description", "reference", "debit", "credit", "balance")

# Other banks' heading wording, per role (hard variants).
SYNONYMS = {
    "txn_date": "Posting Date", "value_date": "Val Dt", "description": "Narration Details",
    "reference": "Chq/Ref No", "debit": "Withdrawals", "credit": "Deposits",
    "balance": "Closing Bal", "ignore": "Sl.",
}


def core_rows(statement):
    return [tuple(str(t.get(k)) for k in CORE) for t in statement["transactions"]]


def score(doc, view, truth_template, truth_statement, roles, mode):
    """(core_identical, extras_identical, validation, error) for one labelling of the table."""
    try:
        layout = llm_view.layout_from_roles(doc, view, roles, mode)
    except llm_view.AnswerError as e:
        return False, False, None, str(e)
    candidate = replace(truth_template, table=layout.spec)
    res = extract_with_template(doc, candidate, None, strategy="candidate")
    got = snapshot(statement_to_dict(res.statement))
    core = core_rows(got) == core_rows(truth_statement)
    extras = [t.get("extra") for t in got["transactions"]] == [t.get("extra") for t in truth_statement["transactions"]]
    return core, extras, validate_statement(res.statement).status.value, ""


def roles_of(template):
    return [c.role for c in sorted(template.table.columns, key=lambda c: c.x0)]


# --- cases ---------------------------------------------------------------------------------

def golden_cases(settings):
    db = Database(settings.db_path)
    registry = {t.template_id: t for t in build_registry(settings).all(include_unapproved=True)}
    for g in db.active_goldens():
        path = settings.documents_dir / f"{g['document_id']}.pdf"
        _, password = reopen(path, password_candidates(settings))
        with open_pdf(path, password, settings.pdf_backend) as doc:
            pages = [doc.page(n) for n in range(1, doc.page_count + 1)]
        yield f"golden-{g['document_id'][:8]}", "base", JsonLayoutDocument(pages), registry[g["template_id"]], g["snapshot"]


def fixture_case(name):
    _, doc = load_layout(FIXTURES / name / "layout.json")
    t = template_from_dict(json.loads((FIXTURES / name / "template.json").read_text(encoding="utf-8")))
    out = json.loads((FIXTURES / name / "output.json").read_text(encoding="utf-8"))
    return doc, t, out["statement"]


def synthetic_cases(settings):
    templates = build_registry(settings).all()
    for path in sorted(settings.synthetic_samples_dir.glob("*.pdf")):
        with open_pdf(path, None, settings.pdf_backend) as doc:
            pages = [doc.page(n) for n in range(1, doc.page_count + 1)]
            best = extract_known(doc, detection_text(doc), templates)
        if best is None:
            continue
        t, result, _ = best
        yield f"synthetic-{path.stem}", "base", JsonLayoutDocument(pages), t, snapshot(statement_to_dict(result.statement))


def renamed_headings(doc, template):
    """The copy with each column's heading words replaced by other banks' wording (same positions)."""
    toks = set(template.table.header_tokens)
    cols = sorted(template.table.columns, key=lambda c: c.x0)
    pages = []
    for n in range(1, doc.page_count + 1):
        page = doc.page(n)
        words = list(page.words)
        for line in group_lines(page.words, 3.0):
            if len(line.words) < 2 or not all(w.text in toks for w in line.words):
                continue
            for c in cols:
                ws = [w for w in line.words if c.x0 <= w.x_mid < c.x1]
                if not ws:
                    continue
                for w in ws:
                    words.remove(w)
                x, h = ws[0].x0, ws[0]
                cw = sum(w.x1 - w.x0 for w in ws) / max(sum(len(w.text) for w in ws), 1)
                # Keep the new wording inside the old heading's share of the line.
                room = max(w.x1 for w in ws) - x + 0.5 * (c.x1 - max(w.x1 for w in ws))
                cw = min(cw, room / len(SYNONYMS[c.role]))
                for part in SYNONYMS[c.role].split():
                    words.append(Word(part, x, x + cw * len(part), h.top, h.bottom))
                    x += cw * (len(part) + 1)
        pages.append(PageLayout(page.number, page.width, page.height, tuple(words)))
    toks_new = tuple(dict.fromkeys(p for c in cols for p in SYNONYMS[c.role].split()))
    return JsonLayoutDocument(pages), replace(template, table=replace(template.table, header_tokens=toks_new))


def all_cases(settings, with_goldens):
    if with_goldens and settings.db_path.exists():
        yield from golden_cases(settings)
    for name in ("boi-23f4f42a", "icici-ec27b806"):
        doc, t, out = fixture_case(name)
        yield f"copy-{name}", "base", doc, t, out
        hdoc, ht = renamed_headings(doc, t)
        yield f"renamed-{name}", "hard", hdoc, ht, out
    doc, t, out = fixture_case("sbi-d2e542a8")
    yield "copy-sbi-d2e542a8 (no heading text)", "hard", doc, t, out
    yield from synthetic_cases(settings)


# --- run -----------------------------------------------------------------------------------

def run(model, with_goldens=True, repeats=2, only=""):
    settings = Settings.from_env()
    client = OllamaClient(timeout=600)
    if not client.has_model(model):
        sys.exit(f"model {model} is not installed (ollama pull {model})")
    rows = []
    cases = [c for c in all_cases(settings, with_goldens) if only in c[0]]
    out = settings.data_dir / "exports" / "llm-choice"
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "progress.log").open("w", encoding="utf-8")

    def progress(text):
        print(text, flush=True)
        log.write(text + "\n")
        log.flush()

    progress(f"{model}: {len(cases)} cases x {repeats} runs")
    for i, (name, kind, doc, truth_t, truth_s) in enumerate(cases, 1):
        progress(f"[{i}/{len(cases)}] {name} ...")
        try:
            view = llm_view.find_first_table(doc)
        except llm_view.AnswerError as e:
            rows.append({"case": name, "kind": kind, "error": f"table not found: {e}", "core_identical": False,
                         "repeatable": True, "seconds": [0]})
            progress(f"[{i}/{len(cases)}] {name:38} table not found")
            continue
        words = llm_view.word_roles(view)
        words_core = bool(words) and score(doc, view, truth_t, truth_s, *words)[0]
        answers, times, tokens = [], [], []
        for _ in range(repeats):
            t0 = time.perf_counter()
            try:
                reply = client.chat_json(model, llm_view.SYSTEM, llm_view.build_prompt(view), llm_view.answer_schema(view))
                answers.append(reply.data)
                tokens.append(reply.tokens)
            except LLMError as e:
                answers.append({"_error": str(e)})
            times.append(round(time.perf_counter() - t0, 1))
        a = answers[0]
        roles_ok, core, extras, status, err = False, False, False, None, a.get("_error", "")
        if not err:
            try:
                roles, mode = llm_view.roles_from_answer(view, a)
                roles_ok = roles == roles_of(truth_t)
                core, extras, status, err = score(doc, view, truth_t, truth_s, roles, mode)
                if status == "FAIL" and {"debit", "credit"} <= set(roles):
                    # What the agent does next: swap debit/credit once, the balance chain decides.
                    roles = ["credit" if r == "debit" else "debit" if r == "credit" else r for r in roles]
                    core, extras, status, err = score(doc, view, truth_t, truth_s, roles, mode)
                    err = (err + " " if err else "") + "(after debit/credit swap)"
                    roles_ok = roles == roles_of(truth_t)
            except llm_view.AnswerError as e:
                err = str(e)
        row = {"case": name, "kind": kind, "columns": len(view.headings), "words_core_identical": words_core,
               "json_ok": "_error" not in a, "roles_ok": roles_ok, "core_identical": core,
               "extras_identical": extras, "validation": status,
               "repeatable": all(x == answers[0] for x in answers), "seconds": times, "tokens": tokens, "error": err}
        rows.append(row)
        progress(f"[{i}/{len(cases)}] {name:38} {kind:4}  words {'same' if words_core else '-   '}"
                 f"  model: roles {'ok ' if roles_ok else 'BAD'} core {'same' if core else 'DIFF'}"
                 f"  extras {'same' if extras else 'diff'}  {status or '-':4}  repeat {'yes' if row['repeatable'] else 'NO '}"
                 f"  {times}s  {err}")
    try:
        ps = client._call("/api/ps", timeout=5).get("models", [])
        mem = next((round(m.get("size", 0) / 1e9, 2) for m in ps if m.get("name") == model), None)
    except LLMError:
        mem = None
    base = [r for r in rows if r["kind"] == "base"]
    hard = [r for r in rows if r["kind"] == "hard"]
    warm = [s for r in rows for s in r["seconds"][1:]]
    summary = {"model": model, "words_only_identical": f"{sum(r.get('words_core_identical', False) for r in rows)}/{len(rows)}",
               "base_identical": f"{sum(r['core_identical'] for r in base)}/{len(base)}",
               "hard_identical": f"{sum(r['core_identical'] for r in hard)}/{len(hard)}",
               "repeatable": all(r["repeatable"] for r in rows),
               "first_call_s": rows[0]["seconds"][0] if rows else None,
               "median_warm_s": statistics.median(warm) if warm else None,
               "max_s": max((s for r in rows for s in r["seconds"]), default=None), "memory_gb": mem}
    progress("done: " + json.dumps(summary))
    log.close()
    (out / f"{model.replace(':', '_')}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2),
                                                         encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--no-goldens", action="store_true", help="only anonymised and synthetic cases")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--only", default="", help="only cases whose name contains this")
    args = ap.parse_args()
    run(args.model, not args.no_goldens, args.repeats, args.only)
