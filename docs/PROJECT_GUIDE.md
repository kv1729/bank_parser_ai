# Project Guide

*How the bank statement parser works, what is used where, where data lives, and how to find your way around. Updated 2026-10-04.*

**Status:** stages 1–4 are done; stage 5 (documentation) is in progress. The live tracker is [`STAGES.md`](../STAGES.md).

## 1. What it does

You upload a text-based bank statement PDF. The system returns:

- the account and statement details (bank, holder, account number, addresses, IFSC, period, balances);
- every transaction (date, description, reference, debit or credit, amount, running balance, page);
- a validation report saying whether the numbers add up.

It knows a statement layout through a **template**. When a layout is new, it still extracts the statement immediately. Meanwhile it learns a template for that layout in the background, so the next statement of that layout is faster and more reliable.

## 2. Your questions, answered

| Question | Answer |
| --- | --- |
| Is there an agent that creates templates for new bank PDFs? | **Yes — the template agent** (`bank_parser/learning/agent.py`). It runs in the background as a bounded loop: propose a template → extract with it → validate → regression-test → save as a new version, or hand over to a human. Limits: 3 candidates, 2 refinements, 300 seconds. |
| Does that agent use an LLM? | **No.** Its proposer (`HeuristicProposer`) is deterministic code. A slot for an LLM proposer exists (`LLMProposer`) but is disabled, because no layout so far has needed one. |
| Is there another agent that extracts data from new PDFs? | **No — that part is deliberately not an agent.** A new layout is extracted by ordinary deterministic code (`bank_parser/extraction/unknown.py`, `inference.py`), immediately, without waiting for the template agent. The balance arithmetic decides whether it got it right. |
| Were LLMs used anywhere? | **Not in the running product.** In development, Claude (Opus 5.5, in Claude Code) wrote the code and documents. The old prototype file `llm.py` calls a local Ollama model (`qwen:4b`); the new system does not use it. |
| Were MCP servers used? | **Not in the product.** In development: **Context7** (MCP plugin) was used twice to check pdfplumber documentation (`Page.close()`, `password=`). The **Claude Docs** connector was used once to publish the first review as a shareable page, before it moved to `docs/ARCHITECTURE_REVIEW.md`. Debugger MCP was not available. |
| Were skills or plugins used? | **In development only:** the **archify** skill drew the diagrams in `docs/diagrams/`, and **Context7** (a plugin) as above. No skill or plugin runs inside the product. |
| Which libraries does the product use? | pdfplumber (reads PDFs; MIT licence), optional PyMuPDF (15× faster; AGPL, decision pending), FastAPI + uvicorn + python-multipart (web app), Python's `sqlite3` and `decimal`. For tests: pytest and httpx. |
| Does any data leave the machine? | **No.** All processing is local. Nothing is sent to an LLM or an external service. |

## 3. How one statement is processed

Interactive diagrams (open in a browser):

- [System overview](diagrams/architecture-overview/bank-parser-architecture.html)
- [Processing workflow](diagrams/document-workflow/document-workflow.html)

| Step | What happens | Code |
| --- | --- | --- |
| 1. Upload | The file is streamed to disk in 1 MiB chunks. Its SHA-256 hash becomes the document ID, so the same file twice is the same document. | `app/main.py`, `storage/files.py` |
| 2. Classify | The file is checked: does it have a text layer, is it password-protected, is it a valid PDF? Scanned PDFs stop here as `NEEDS_OCR`. | `pdf/classify.py`, `pipeline.py` |
| 3. Detect layout | Field labels on pages 1, 2 and the last page are compared with every template's *markers*. | `detection.py` |
| 4a. Known layout | The matching template drives extraction. | `extraction/engine.py`, `extraction/table.py`, `extraction/header.py` |
| 4b. Unknown layout | **Workflow 2 starts in the background** (template agent). **Workflow 1 extracts now**: it finds the column positions from the table's ruled cells, works out which column is which from header words or content, and uses the balance arithmetic to tell debit from credit. | `extraction/unknown.py`, `extraction/inference.py`, `learning/` |
| 5. Validate | Each row is checked: previous balance − debit + credit = balance. Totals are checked against the summary the bank prints, along with dates and IFSC. | `validation.py` |
| 6. Store | The result, validation report and timings are saved to SQLite. Status is `EXTRACTED` only if the arithmetic verified. | `pipeline.py`, `storage/db.py` |
| 7. Show | The web page polls for status and shows the header, checks and transactions. | `app/static/index.html` |
| 8. After learning | If the agent saved a template, the document is re-extracted with it, and future statements of that layout take step 4a. | `pipeline.py` (`_learning_done`) |

## 4. Deterministic vs agentic

| Kind | Parts |
| --- | --- |
| Deterministic (plain code, same input → same output) | Upload, hashing, classification, layout detection, template extraction, unknown-layout extraction, validation, storage, web app |
| Agentic (bounded search with acceptance criteria) | Template agent only |
| LLM | None today |

## 5. Code map

| Folder / file | What it is |
| --- | --- |
| `bank_parser/pdf/` | Opens PDFs and returns words with positions. Two interchangeable backends; also handles classification and passwords. |
| `bank_parser/schema.py` | The output format: `Statement`, `Transaction`, etc. Money is `Decimal`. |
| `bank_parser/templates/` | What a template is (`model.py`) and where templates are stored (`registry.py`, never overwritten). |
| `bank_parser/detection.py` | Which template fits a document. |
| `bank_parser/extraction/` | `table.py` builds rows from words; `header.py` applies header rules; `engine.py` runs a template; `inference.py` + `unknown.py` handle new layouts; `generic_rules.py` holds bank-independent header patterns. |
| `bank_parser/validation.py` | All checks and the report. |
| `bank_parser/learning/` | `agent.py` (template agent), `proposers.py` (how candidates are proposed), `markers.py` (privacy-safe layout labels). |
| `bank_parser/storage/` | File store and SQLite. |
| `bank_parser/pipeline.py` | Orchestrates everything, with separate worker pools for extraction and learning. |
| `bank_parser/app/` | FastAPI server and the single web page. |
| `bank_parser/config.py` | Settings (environment variables). |
| `template_registry/` | Reviewed templates, committed to git. |
| `tests/` | 139 tests. `test_pipeline.py` proves extraction never waits for learning. |
| `experiments/` | `benchmark.py` (timings per stage) and the first real-statement experiment. |
| Root `*.py` (`parser.py`, `llm.py`, …) | The original prototype, kept for reference; not used. |

## 6. Where the data is

| What | Where | In git? |
| --- | --- | --- |
| Uploaded PDFs | `data/documents/<sha256>.pdf` | No |
| Results, validation reports, jobs | `data/bank_parser.db` (SQLite: `documents`, `extractions`, `template_jobs`) | No |
| Templates learned at runtime | `data/templates/<bank>/<layout>/vN.json` | No (promote after review) |
| Reviewed templates | `template_registry/<bank>/<layout>/vN.json` | Yes |
| Your real statement and its password | `sample_data/AccountStatement_…pdf`, `sample_data/.pdf_password` | No |
| Hand-verified expected outputs | `tests/fixtures/*.expected.json` | No |
| Synthetic sample PDFs | `sample_data/*.pdf` | Yes (PII question open: D-004) |

To look inside the database: `uv run python -c "import sqlite3; c=sqlite3.connect('data/bank_parser.db'); print(c.execute('select id, status from documents').fetchall())"`.

## 7. Everyday commands

| Task | Command |
| --- | --- |
| Install | `uv sync` (add `--extra fast` for PyMuPDF) |
| Run tests | `uv run pytest` |
| Start the app | `uv run uvicorn bank_parser.app.main:app --host 127.0.0.1 --port 8000`, then open http://127.0.0.1:8000 |
| Time each stage | `uv run python experiments/benchmark.py` |
| See templates | open `template_registry/` and `data/templates/`, or http://127.0.0.1:8000/api/templates |

## 8. Which document is for what

| File | Purpose | Changes when |
| --- | --- | --- |
| `README.md` | What the project is and how to start it | Setup or scope changes |
| `STAGES.md` | **Where we are**: stages, status, exit criteria | Every working session |
| `docs/PROJECT_GUIDE.md` | **How it works** in plain language (this file) | A component or tool is added or removed |
| `docs/ARCHITECTURE.md` | Technical design and measured latency | The design changes |
| `docs/diagrams/` | Interactive diagrams (archify) | The design changes; re-render from `candidate.json` |
| `DECISIONS.md` | **Why**: each decision with evidence and rejected options | A meaningful decision is made |
| `Bank_Statement_Parser_PLAN.md` | The long-term plan and principles | Direction changes |
| `INSTRUCTIONS.md` | The product brief you gave | You change the brief |
| `CLAUDE.md` | Rules and commands for Claude when working in this repo | A working rule changes |
| `docs/ARCHITECTURE_REVIEW.md`, `PLAN.md` | History: the first review and its prompt | Never (archive) |

## 9. Glossary

- **Layout:** how a bank arranges a statement (columns, labels). One bank can have several layouts.
- **Template:** the versioned JSON description of one layout that the engine executes. A new version is a new file; old versions stay.
- **Marker:** a field label (e.g. "IFSC Code") used to recognise a layout. Built only from a fixed vocabulary, so never personal data.
- **Balance chain:** the check that every row's balance equals the previous balance minus the debit plus the credit.
- **Reconciliation:** the check that extracted totals and counts equal the totals the bank prints.
- **Workflow 1 / Workflow 2:** extraction now / template learning in the background.
- **Verified:** at least one arithmetic check passed and nothing failed.
