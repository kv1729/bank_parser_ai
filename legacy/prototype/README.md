# Legacy prototype (archived)

The original prototype, kept for reference only. Nothing in `backend/` imports it, and it is not maintained.

| File | What it did | Replaced by |
| --- | --- | --- |
| `parser.py` | Text extraction (pdfminer/pdfplumber) with a fixed cache path; line-count "segmentation" | `backend/src/bank_parser/pdf/`, `extraction/` |
| `heuristics.py`, `extractor.py` | Regexes for 3 header fields; LLM call for the holder name | `extraction/generic_rules.py`, `extraction/header.py` |
| `llm.py`, `model_testing.py` | Ollama (`qwen:4b`) calls and a latency check | Not used; see `docs/DECISIONS.md` D-011 |
| `utils.py` | JSON helpers, metrics | `storage/`, `scripts/benchmark.py` |
| `requirements*.txt` | Old dependency pins | `backend/pyproject.toml` + `uv.lock` |

The review of this prototype is in `docs/history/ARCHITECTURE_REVIEW.md`.
