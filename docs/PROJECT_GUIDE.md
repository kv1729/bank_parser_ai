# Project Guide

*How the bank statement parser works, what is used where, where data lives, and how to find your way around. Updated 2026-10-04 (after the Stage 6 restructure). Code paths below are relative to `backend/src/bank_parser/` unless they start with a top-level folder.*

**Status:** see the live tracker, [`STAGES.md`](../STAGES.md).

## 1. What it does

You upload a text-based bank statement PDF. The system returns:

- the account and statement details (bank, holder, account number, addresses, IFSC, period, balances);
- every transaction (date, description, reference, debit or credit, amount, running balance, page);
- a validation report saying whether the numbers add up.

It knows a statement layout through a **template**. When a layout is new, it still extracts the statement immediately. Meanwhile it learns a template for that layout in the background, so the next statement of that layout is faster and more reliable.

## 2. Your questions, answered

| Question | Answer |
| --- | --- |
| Is there an agent that creates templates for new bank PDFs? | **Yes — the template agent** (`learning/agent.py`). It runs in the background as a bounded loop: propose a template → extract with it → self-check → check it does not mistake other banks' statements → save as a new version, or hand over to a human. Limits: 3 candidates, 2 refinements, 300 seconds. **It learns layouts with a ruled table** (the date may be in any of the first columns; currency symbols, header on page 1 only and newest-first order are handled, D-020), **and, since Stage 10, tables without ruled lines**: code finds the rows and columns from the word positions; familiar heading words name the columns, otherwise a **local AI model** (`qwen3.5:9b` via Ollama, on this computer) names them. Every proposal passes the same checks before it is saved (D-023). |
| Does that agent use an LLM? | **Yes, a local one, last.** The proposers run in order: fixed rules (`HeuristicProposer`), the headings of tables you approved (`ExamplesProposer`, D-027), then the local model (`LLMProposer`, `qwen3.5:9b` via Ollama on this computer, D-023). No agent framework is used: the loop and the model client are the project's own code. |
| Is there another agent that extracts data from new PDFs? | **Not yet.** A new layout is extracted immediately by fixed rules (`extraction/unknown.py`, `inference.py`) — the same rules the template agent uses, so when they fail, both fail. **Planned (Stage 10):** when no rule or template fits, the local AI agent extracts the statement itself (status `LEARNING` meanwhile), and the result passes the same self-check. |
| "Regression test" — which one? | Two different checks (D-019). **Self-check:** every new statement is checked against itself (balance chain, printed totals, dates) — proves numbers, not text. **Regression:** every engine or template change re-extracts all approved ("golden") statements and must reproduce them exactly — Stage 9. |
| Were LLMs used anywhere? | **Not in the running product.** In development, Claude (Opus 5.5, in Claude Code) wrote the code and documents. The archived prototype file `legacy/prototype/llm.py` calls a local Ollama model (`qwen:4b`); the new system does not use it. |
| Were MCP servers used? | **Not in the product.** In development: **Context7** (MCP plugin) was used twice to check pdfplumber documentation (`Page.close()`, `password=`). The **Claude Docs** connector was used once to publish the first review as a shareable page, before it moved to `docs/history/ARCHITECTURE_REVIEW.md`. Debugger MCP was not available. |
| Were skills or plugins used? | **In development only:** the **archify** skill drew the diagrams in `docs/diagrams/`, and **Context7** (a plugin) as above. No skill or plugin runs inside the product. |
| Which libraries does the product use? | **pypdfium2** (default PDF reader: Google's PDFium, free Apache/BSD licence, ~18× faster than pdfplumber; D-015), pdfplumber (MIT; used for ruled-table geometry when learning a new layout), optional PyMuPDF (AGPL; not needed), FastAPI + uvicorn + python-multipart (web app), Python's `sqlite3` and `decimal`. For tests: pytest and httpx. |
| Why not paid bank-statement MCP servers (Bankstatemently, DocuClipper)? | They are paid cloud services (≈$0.03–0.19 per page) that receive your statements, and via MCP they put an LLM in every request. Free alternatives were reviewed in `docs/DECISIONS.md` D-016: **Docling** (IBM, MIT, offline) is the one worth testing, for layouts we cannot read and for scans, used as a library. |
| Does any data leave the machine? | **No.** All processing is local. Nothing is sent to an LLM or an external service. |

## 3. How one statement is processed

Interactive diagrams (open in a browser):

- [System overview](diagrams/architecture-overview/bank-parser-architecture.html)
- [Processing workflow](diagrams/document-workflow/document-workflow.html)

| Step | What happens | Code |
| --- | --- | --- |
| 1. Upload | The file is streamed to disk in 1 MiB chunks. Its SHA-256 hash becomes the document ID, so the same file twice is the same document. | `api/main.py`, `storage/files.py` |
| 2. Classify | The file is checked: does it have a text layer, is it password-protected, is it a valid PDF? Scanned PDFs stop here as `NEEDS_OCR`. | `pdf/classify.py`, `pipeline.py` |
| 3. Detect layout | Field labels on pages 1, 2 and the last page are compared with every template's *markers*. | `extraction/detection.py` |
| 4a. Known layout | The matching template drives extraction. | `extraction/engine.py`, `extraction/table.py`, `extraction/header.py` |
| 4b. Unknown layout | **Workflow 1 extracts now**: it finds the columns from the table's ruled cells, or (without ruled lines) from the word positions, works out which column is which from heading words or content, and uses the balance arithmetic to tell debit from credit. No AI model here, so this stays fast. **Then Workflow 2 starts in the background** (template agent, which may use the local AI model). If the first result is verified the status is `EXTRACTED`; otherwise it is **`LEARNING`** until the agent has learned the layout, and the statement is then re-extracted with the new template automatically (D-023). | `extraction/unknown.py`, `extraction/inference.py`, `learning/` |
| 5. Validate | Each row is checked: previous balance − debit + credit = balance. Totals are checked against the summary the bank prints, along with dates and IFSC. | `core/validation.py` |
| 6. Store | The result, validation report and timings are saved to SQLite. Status is `EXTRACTED` only if the arithmetic verified. | `pipeline.py`, `storage/db.py` |
| 7. Show | The web page polls for status and shows the header, checks and transactions. | `frontend/index.html` |
| 8. After learning | If the agent saved a template, the document is re-extracted with it, and future statements of that layout take step 4a. | `pipeline.py` (`_learning_done`) |
| 9. Review (you) | At http://127.0.0.1:8000/review you pick a PDF and see it page by page next to the extracted output. Mark each field OK, Wrong or Missing (printed but not extracted), tick wrong transaction rows, write remarks; it auto-saves. Above the fields, the **system's self-review**: what it checked, what it is unsure of, a confidence per field; you mark each doubt Agree/Disagree (D-024). | `frontend/review.html`, `review.py`, `pdf/render.py`, `self_review.py` |
| 10. Golden (you) | When everything is OK, **Approve as golden**: the output is stored as the expected answer. `scripts/regress.py` (also run before every push) and the template agent then refuse any change that alters a golden output. | `golden.py`, `extraction/select.py`, `scripts/regress.py`, `.githooks/pre-push` |
| 11. Anonymised layout (CI) | Each golden layout gets a copy with real positions and fake values, so the public CI checks the engine on real layouts without real data. | `scripts/anonymise_layout.py`, `pdf/json_layout.py`, `tests/fixtures/layouts/` |
| 12. Local AI model (Stage 10) | For a table without ruled lines whose headings are unfamiliar (or missing), the template agent shows a local model each column's heading and three sample values; the model answers with one role per column. Code checks the labels against the contents and the arithmetic, tries debit/credit the other way round once, and saves the template only if every check passes. Runs on this computer only. | `learning/llm_view.py`, `learning/proposers.py` (`LLMProposer`), `llm/ollama.py`, `experiments/llm_choice.py` |
| 13. Learning from your reviews (Stage 10.7) | Approvals, marks and Agree/Disagree are stored as examples (never values). Headings of approved tables label new tables without the model; the self-review is less sure of fields you corrected before. | `learning/examples.py`, `storage/db.py` (`training_examples`), `self_review.py` (`apply_history`), `scripts/build_examples.py` |
| 14. Autonomy (Stage 11) | Each layout builds a track record from your reviews. A statement needs you unless it is confidently right and its layout has earned it (10 reviews in a row without a correction). With autonomy switched on, the agent then approves it as golden itself. A bank that changes its layout is noticed and learned again. | `autonomy.py`, `self_review.py` (`with_confidence`), `golden.py` (`check_agent_approvable`), `frontend/autonomy.html` |

## 3a. Verifying an extraction yourself (review screen)

1. Start the app (section 7) and open http://127.0.0.1:8000/review.
2. **Left:** the PDFs you can review — your private statements in `sample_data/`, the synthetic samples, and anything uploaded. A file not yet imported is imported and processed when you click it. A password-protected file uses `sample_data/.pdf_password` if present; otherwise the page asks for the password (kept in memory only).
3. **Middle:** the PDF, page by page; scroll freely.
4. **Right:** what was extracted. For each header field and printed total choose **OK**, **Wrong**, or **Missing** (printed on the statement but not extracted). Tick every transaction row that is wrong. Write your observations in **Remarks**. It saves automatically; the bar at the bottom always shows **Saved ✓** or **Save failed: …**, and the list on the left shows how many items you marked.
5. Click a transaction to jump the PDF to its page. Rows with an amber edge are ones the validator itself questioned.
6. After a fix, use **Re-extract with current code** (next to the extraction number) to get fresh output for the same PDF. Your earlier review stays attached to the earlier extraction.

Your reviews stay in `data/bank_parser.db` (gitignored) and become the hand-checked answers for the Stage 7 accuracy report.

## 4. Deterministic vs agentic

| Kind | Parts |
| --- | --- |
| Deterministic (plain code, same input → same output) | Upload, hashing, classification, layout detection, template extraction, unknown-layout extraction, validation, storage, web app |
| Agentic (bounded search with acceptance criteria) | Template agent only |
| LLM | None today |

## 5. Code map

| Folder / file | What it is |
| --- | --- |
| `backend/` | The Python service: its own uv project (`pyproject.toml`, `uv.lock`). Run Python commands from here. |
| `backend/src/bank_parser/core/` | The domain: output format (`schema.py`: `Statement`, `Transaction`, `Decimal` money) and all checks (`validation.py`). No file or network access. |
| `…/pdf/` | Opens PDFs and returns words with positions. Two interchangeable backends; classification and passwords. |
| `…/templates/` | What a template is (`model.py`) and where templates are stored (`registry.py`, never overwritten). |
| `…/extraction/` | `detection.py` picks a template; `table.py` builds rows from words; `header.py` applies header rules (`regex`, positional `region`, labelled multi-line `block`); `engine.py` runs a template; `select.py` picks which template extracts a document (shared by the app, `regress` and the agent); `inference.py` + `unknown.py` handle new layouts; `generic_rules.py` holds bank-independent header patterns. |
| `…/learning/` | `agent.py` (template agent), `proposers.py` (how candidates are proposed), `markers.py` (privacy-safe layout labels), `llm_view.py` (tables without ruled lines; what the local model sees). |
| `…/llm/` | `ollama.py`: the local model client (this computer only). |
| `…/storage/` | File store and SQLite. |
| `…/pipeline.py` | Orchestrates everything, with separate worker pools for extraction and learning. |
| `…/api/main.py` | FastAPI routes (the backend's HTTP interface). |
| `…/config.py` | Settings (environment variables) and folder locations. |
| `backend/template_registry/` | Reviewed templates, committed: this system's equivalent of trained models. |
| `backend/tests/` | 139 tests. `test_pipeline.py` proves extraction never waits for learning; `fixtures/pdfs/` holds the synthetic samples. |
| `backend/scripts/` | `benchmark.py` (timings per stage), `refresh_sensitive_strings.py` (privacy list). |
| `backend/experiments/` | One-off research (the first real-statement experiment). |
| `frontend/` | The web page (`index.html`), served by the API. Becomes a full frontend project if a JavaScript UI is built. |
| `scripts/privacy_check.py` + `.githooks/` | Privacy gate run on every commit. |
| `.github/workflows/ci.yml` | CI: runs the tests and the privacy rules on every push. |
| `legacy/prototype/` | The original prototype, archived; not used. |

## 6. Where the data is

| What | Where | In git? |
| --- | --- | --- |
| Uploaded PDFs | `data/documents/<sha256>.pdf` | No |
| Results, validation reports, jobs, your reviews | `data/bank_parser.db` (SQLite: `documents`, `extractions`, `template_jobs`, `reviews`) | No |
| Templates learned at runtime | `data/templates/<bank>/<layout>/vN.json` | No (promote after review) |
| Reviewed templates | `backend/template_registry/<bank>/<layout>/vN.json` | Yes |
| Your private statements and password | `sample_data/` (whole folder) | No |
| Hand-verified expected outputs | `backend/tests/fixtures/*.expected.json` | No |
| Synthetic sample PDFs | `backend/tests/fixtures/pdfs/` | Yes (PII question open: D-004) |
| Local list of identifiers the privacy gate blocks | `.git/info/sensitive-strings` | No (inside `.git`) |

To look inside the database (from `backend/`): `uv run python -c "import sqlite3; c=sqlite3.connect('../data/bank_parser.db'); print(c.execute('select id, status from documents').fetchall())"`.

## 7. Everyday commands (from `backend/`)

| Task | Command |
| --- | --- |
| Install | `uv sync` (`--all-extras` adds the optional PyMuPDF backend; not needed) |
| Run tests | `uv run pytest` |
| Start the app | Double-click `start-app.cmd` (repository root), then open http://127.0.0.1:8000 (upload) or http://127.0.0.1:8000/review (verify). Full steps and troubleshooting: `docs/RUNBOOK.md` |
| Time each stage | `uv run python scripts/benchmark.py` |
| Export results to a spreadsheet | `uv run python scripts/export_extraction.py --doc <id-prefix>` → CSVs in `data/exports/` (see `docs/RUNBOOK.md` §4a) |
| Refresh the privacy list | `uv run python scripts/refresh_sensitive_strings.py` |
| See templates | `backend/template_registry/` and `data/templates/`, or http://127.0.0.1:8000/api/templates |

## 8. Which document is for what

| File | Purpose | Changes when |
| --- | --- | --- |
| `README.md` | What the project is, layout, how to start | Setup or scope changes |
| `STAGES.md` | **Where we are**: stages, status, exit criteria | Every working session |
| `docs/PROJECT_GUIDE.md` | **How it works** in plain language (this file) | A component or tool is added or removed |
| `docs/ARCHITECTURE.md` | Technical design and measured latency | The design changes |
| `docs/diagrams/` | Interactive diagrams (archify) | The design changes; re-render from `candidate.json` |
| `docs/DECISIONS.md` | **Why**: each decision with evidence and rejected options | A meaningful decision is made |
| `docs/PLAN.md` | The long-term plan and principles | Direction changes |
| `docs/PRODUCT_BRIEF.md` | The product brief you gave | You change the brief |
| `CLAUDE.md` | Rules and commands for Claude when working in this repo | A working rule changes |
| `docs/history/` | The first review and its prompt | Never (archive) |

## 9. Glossary

- **Layout:** how a bank arranges a statement (columns, labels). One bank can have several layouts.
- **Template:** the versioned JSON description of one layout that the engine executes. A new version is a new file; old versions stay.
- **Marker:** a field label (e.g. "IFSC Code") used to recognise a layout. Built only from a fixed vocabulary, so never personal data.
- **Balance chain:** the check that every row's balance equals the previous balance minus the debit plus the credit.
- **Reconciliation:** the check that extracted totals and counts equal the totals the bank prints.
- **Workflow 1 / Workflow 2:** extraction now / template learning in the background.
- **Verified:** at least one arithmetic check passed and nothing failed.
