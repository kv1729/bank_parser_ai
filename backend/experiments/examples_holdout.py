"""
Stage 10.7 (D-027): do the owner's approved tables help label new ones?

Leave-one-out over the Stage 10.1 cases (experiments/llm_choice.py): for each case, the
approved examples come from every *other* case (its own table is never an example), as
if that statement were the next new one.

  1. vocabulary: tables labelled with no model call, by the familiar heading words
     (baseline) and by those plus the headings of approved examples; any labelling
     that passes the checks but reads the table wrongly is counted (must be 0).
  2. few-shot (--model): the local model with and without the nearest approved
     examples in its prompt: identical tables, tokens, seconds.

Prints scores only; never statement text. Report: data/exports/examples-holdout/<name>.json

    uv run python experiments/examples_holdout.py [--model qwen3.5:9b] [--no-goldens]
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_choice import all_cases, roles_of, score  # noqa: E402

from bank_parser.config import Settings  # noqa: E402
from bank_parser.learning import llm_view  # noqa: E402
from bank_parser.learning.examples import few_shot, nearest, table_example, vocabulary, vocabulary_roles  # noqa: E402
from bank_parser.llm.ollama import LLMError, OllamaClient  # noqa: E402


def labelled(doc, view, truth_t, truth_s, roles):
    """'same' | 'wrong-but-passes' | 'rejected' for one labelling (None = no labelling)."""
    if roles is None:
        return None
    core, _, status, err = score(doc, view, truth_t, truth_s, *roles)
    if core:
        return "same"
    return "rejected" if err or status == "FAIL" else "wrong-but-passes"


def ask(client, model, view, examples_text):
    t0 = time.perf_counter()
    try:
        reply = client.chat_json(model, llm_view.SYSTEM, llm_view.build_prompt(view, examples_text),
                                 llm_view.answer_schema(view))
    except LLMError as e:
        return None, 0, round(time.perf_counter() - t0, 1), str(e)
    try:
        roles = llm_view.roles_from_answer(view, reply.data)
    except llm_view.AnswerError as e:
        return None, reply.tokens, round(time.perf_counter() - t0, 1), str(e)
    return roles, reply.tokens, round(time.perf_counter() - t0, 1), ""


def main(model, with_goldens):
    settings = Settings.from_env()
    cases = []
    for name, kind, doc, truth_t, truth_s in all_cases(settings, with_goldens):
        try:
            view = llm_view.find_first_table(doc)
        except llm_view.AnswerError:
            view = None
        ex = table_example(doc, truth_t) if view else None
        cases.append((name, kind, doc, truth_t, truth_s, view, ex))
    client = OllamaClient(timeout=600) if model else None
    if model and not client.has_model(model):
        sys.exit(f"model {model} is not installed")
    rows = []
    for i, (name, kind, doc, truth_t, truth_s, view, ex) in enumerate(cases):
        row = {"case": name, "kind": kind, "table_found": view is not None, "example_recorded": ex is not None}
        if ex is not None:
            # The recorded roles must be the template's own (in column order, where the columns match).
            row["example_roles_match_template"] = [r for r in ex["roles"] if r != "ignore"] == \
                [r for r in roles_of(truth_t) if r != "ignore"]
        if view is None:
            rows.append(row)
            print(f"{name:40} table not found", flush=True)
            continue
        others = [{"payload": c[6]} for j, c in enumerate(cases) if j != i and c[6] is not None]
        words = llm_view.word_roles(view)
        learned = vocabulary_roles(view, vocabulary(others))
        row["words"] = labelled(doc, view, truth_t, truth_s, words)
        row["examples"] = labelled(doc, view, truth_t, truth_s, learned)
        # What the agent does: heading words first, then the approved examples.
        row["no_model"] = row["words"] if row["words"] == "same" else (row["examples"] or row["words"])
        near = nearest(others, view)
        row["nearest_examples"] = len(near)
        if model:
            for label, text in (("plain", ""), ("few_shot", few_shot(near))):
                roles, tokens, secs, err = ask(client, model, view, text)
                row[f"model_{label}"] = labelled(doc, view, truth_t, truth_s, roles) or f"no answer: {err}"
                row[f"model_{label}_tokens"], row[f"model_{label}_s"] = tokens, secs
        rows.append(row)
        print(f"{name:40} words {row['words'] or '-':16} examples {row['examples'] or '-':16} "
              + (f"model {row['model_plain']:16} few-shot {row['model_few_shot']:16} "
                 f"tokens {row['model_plain_tokens']}/{row['model_few_shot_tokens']}" if model else ""), flush=True)
    found = [r for r in rows if r["table_found"]]
    summary = {
        "cases": len(rows),
        "examples_recorded": sum(r["example_recorded"] for r in rows),
        "example_roles_match_template": sum(r.get("example_roles_match_template", False) for r in rows),
        "no_model_words_only": f"{sum(r['words'] == 'same' for r in found)}/{len(rows)}",
        "no_model_with_examples": f"{sum(r['no_model'] == 'same' for r in found)}/{len(rows)}",
        "wrong_but_passes": sum(r["no_model"] == "wrong-but-passes" for r in found),
    }
    if model:
        for label in ("plain", "few_shot"):
            summary[f"model_{label}"] = f"{sum(r[f'model_{label}'] == 'same' for r in found)}/{len(rows)}"
            summary[f"model_{label}_wrong_but_passes"] = sum(r[f"model_{label}"] == "wrong-but-passes" for r in found)
            summary[f"model_{label}_tokens"] = sum(r[f"model_{label}_tokens"] for r in found)
            summary[f"model_{label}_s"] = round(sum(r[f"model_{label}_s"] for r in found), 1)
    print("done: " + json.dumps(summary), flush=True)
    out = settings.data_dir / "exports" / "examples-holdout"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{(model or 'vocabulary').replace(':', '_')}.json").write_text(
        json.dumps({"summary": summary, "cases": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    ap.add_argument("--no-goldens", action="store_true")
    args = ap.parse_args()
    main(args.model, not args.no_goldens)
