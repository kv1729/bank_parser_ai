# Decision Log

Lightweight record of meaningful decisions (see `Bank_Statement_Parser_PLAN.md` §22).

---

## D-001 — Schema implementation: stdlib dataclasses

- **Date:** 2026-10-02
- **Question:** How should `Statement` / `Transaction` be typed?
- **Candidates considered:** stdlib `dataclasses`; Pydantic v2; attrs.
- **Experiment:** None. The choice is cheap to reverse and no measurement would change it at this stage.
- **Observed evidence:** Pydantic's default (lax) mode coerces floats and numeric strings into `Decimal`, which is exactly the silent path we want to forbid; strict mode fixes that but adds a dependency for no Phase 0 benefit.
- **Decision:** Frozen stdlib dataclasses, plus an explicit parse boundary (`statement_from_dict`) that rejects floats, display-formatted amounts (`1,20,749.89`), more than 2 decimal places, bad dates and wrong types with `SchemaError` naming the field path.
- **Reason:** Zero dependencies, immutable values, money can only enter as a decimal string.
- **Rejected alternatives:** Pydantic (revisit in Phase 5 if an HTTP API needs JSON Schema generation); attrs (no advantage over dataclasses here).
- **Follow-up:** Add template/version/provenance metadata to `Statement` when the first template exists (Phase 1).

## D-002 — SBI ground truth: how it was labelled, and a pdfplumber defect found

- **Date:** 2026-10-02
- **Question:** How do we produce trustworthy expected output for `sample_data/SBI_Bank_Statement_Chenna_Reddy.pdf`?
- **Experiment:** Drafted rows from pdfplumber `extract_tables()`, then verified independently: every debit/credit/balance string was located in the raw page text (Indian digit grouping), and the full balance chain was recomputed from the printed opening balance (75,222.71) to the final balance (1,75,208.82). Each row was then reviewed by eye against the PDF text.
- **Observed evidence:** pdfplumber's table extraction **silently drops the 30 Jul 2018 row** (cheque 367952, debit 10,000.00, balance 55,246.99). The row starts at the bottom of page 1 and its description continues on page 2; page 2's table shows only an empty-dated fragment row. The balance chain breaks there without it. It was added to the ground truth from the text layer.
- **Decision:** `tests/fixtures/sbi_sample.expected.json` holds 32 hand-verified rows. Descriptions are the cell lines joined by single spaces; amounts are normalized decimal strings; `page` is the page the row starts on. `closing_balance` is `null` because the statement does not print one.
- **Reason:** The ground truth must not inherit the extractor's bugs.
- **Rejected alternatives:** Accepting pdfplumber's table output as truth (would have encoded the dropped row as correct).
- **Follow-up (Phase 1):** The SBI template must handle rows split across pages (merge an empty-dated continuation into the previous page's last row, and pick up a final row the table finder misses). The balance chain is what detects this. This weakens the "SBI can rely on pdfplumber tables directly" hypothesis in PLAN §10.

## D-003 — Balance-chain validator semantics

- **Date:** 2026-10-02
- **Question:** How should `validate_balance_chain` behave on incomplete or malformed data?
- **Decision:**
  - Exact `Decimal` comparison, no tolerance.
  - Chain starts from `opening_balance`; without it the first row is taken as given (`WARN no_opening_balance`).
  - After a mismatch, continue from the stated balance, so one corrupted amount yields exactly one issue on that row.
  - A row missing its balance is carried forward and verified cumulatively at the next stated balance (`WARN balance_missing`).
  - Malformed values (float, str, NaN, infinity, negative, neither debit nor credit, non-statement input) return `FAIL` with a code; the validator never raises on bad data.
  - Added a fourth status, `SKIP`, for statements that provide no running balance or no rows, so they are not rejected (PLAN §11 capability-based validation).
- **Follow-up:** Opening/closing reconciliation, exactly-one-of debit/credit, date ordering, page continuity and IFSC format are separate validators, not yet written.

## D-004 — Sample data PII — OPEN, needs owner decision

- **Date:** 2026-10-02
- **Status:** Open (PLAN §9 task 10, §18).
- **Facts:** Both sample PDFs (committed in `d507224`) contain a name, postal address, account number, customer/CIF ID, email (HDFC) and a PAN (inside an SBI transaction description). Both PDFs were produced by WeasyPrint 62.3, which suggests regenerated or synthetic documents, but that does not prove the data is fictional. The new fixture `tests/fixtures/sbi_sample.expected.json` repeats the SBI values.
- **Options:** (a) confirm the data is synthetic and keep it; (b) replace with a redacted/synthetic statement and fixture going forward; (c) additionally rewrite git history — destructive, requires explicit approval.
- **Decision:** Not made. Owner to decide.

## D-005 — First real statement: pdfplumber tables + balance chain

- **Date:** 2026-10-02
- **Question:** Does "pdfplumber table extraction + deterministic validation" hold up on a real, non-synthetic statement?
- **Document:** a real SBI savings-account statement (current SBI layout), 9 pages, password-protected, text layer on every page. Gitignored; password in gitignored `sample_data/.pdf_password`. Nothing from it is committed.
- **Candidates considered:** (1) pdfplumber `extract_tables()` using the ruled cell rectangles; (2) word positions with fixed column boundaries — held in reserve, not needed for correctness.
- **Experiment:** `experiments/real_sbi_statement.py` — extract all rows with pdfplumber, normalize into `bank_parser` types, run `validate_balance_chain`, then reconcile against the page-9 "Statement Summary" (brought forward, Dr/Cr counts, total debits/credits, closing balance). Only masked/aggregate output is printed.
- **Observed evidence:**
  - Opens with the password via pdfplumber (`password=`); no extra library needed.
  - 81 transactions over pages 2–8. Balance chain **PASS on all 81 rows**, 0 issues.
  - Page-9 summary: Dr count, Cr count, total debits, total credits and closing balance **all match exactly**.
  - Dates non-decreasing; exactly one of debit/credit on every row; empty cells are printed as `-`.
  - Column headings (except "Balance") are drawn as vector shapes, not text, so **header-text matching cannot identify the columns** in this layout. Column order (debit = col 4, credit = col 5) was confirmed by arithmetic: the swapped order fails all 81 rows.
  - Each page's table ends with an empty filler row (7 found); they contain no text and are ignored.
  - The Ref/Cheque column is `-` on every row of this statement.
  - Timing (median of 10 runs): open 2 ms, layout + table finding **~1,390 ms** (~200 ms/page), normalize 1 ms, validation 0.1 ms; total ~1.4 s. ~~Table finding dominates~~ **Corrected 2026-10-03 (D-007):** plain word extraction alone takes ~1.5 s; the cost is pdfplumber/pdfminer page parsing, not table finding.
- **Decision:** Keep pdfplumber tables as the Phase 1 baseline for ruled SBI layouts. *(Superseded by D-010: rows are now assembled from words; tables supply only column geometry.)* Bank/layout detection must use geometry and labels (cell-rectangle column boundaries, "STATEMENT OF ACCOUNT", "Statement Summary") rather than header text.
- **Reason:** Correct on every invariant the document provides, with no LLM, OCR or external service.
- **Follow-up:**
  - Latency experiment: word positions + fixed column boundaries from the first page's rectangles, expected to be much faster than full table finding; accept only if output is identical.
  - Promote the summary-page checks (counts, totals, closing) into `bank_parser.validation`.
  - Real-document expected output, if wanted, goes in a gitignored folder — never `tests/fixtures/`.
  - Synthetic SBI sample (D-002) and this real one are **different SBI layouts** — the template registry needs a layout ID, not just a bank.

## D-006 — Expected outputs are local-only

- **Date:** 2026-10-03
- **Question:** Should expected-output fixtures be committed? The GitHub repo is public.
- **Decision:** No. `*.expected.json` is gitignored. Tests that need a missing fixture are skipped, not failed; a fresh clone runs 7 tests and skips 62. `tests/fixtures/README.md` records how each fixture was made.
- **Reason:** Expected outputs repeat statement data (names, account numbers, descriptions) in plain text.
- **Rejected alternatives:** Anonymising the fixture and committing it (owner chose to keep outputs out of git entirely).
- **History:** The fixture had been committed locally in `027fa42` on branch `phase0-schema-validation`, which was never pushed. Work was re-committed on a fresh branch `phase0-foundation` from `main`; the old local branch was left untouched.
- **Follow-up:** Consider a fully synthetic, committed fixture so CI can run the validator tests.

## D-007 — PDF library: pdfplumber default, PyMuPDF optional behind a backend interface

- **Date:** 2026-10-03
- **Question:** Which library turns PDF pages into positioned words fast and reliably enough?
- **Candidates considered:** pdfplumber (MIT, already used); PyMuPDF (AGPL-3.0 or commercial licence); pypdf (text only, no reliable word positions — not tested).
- **Experiment:** Same 9-page real statement and both synthetic samples; words per page with coordinates; then the full extraction suite on both backends.
- **Observed evidence:**
  - Word extraction, 9-page real statement: pdfplumber **1,513 ms**, PyMuPDF **102 ms** (15×). Synthetic SBI: 141 vs 27 ms; HDFC: 83 vs 4 ms.
  - Same words found (1,700 vs 1,718; PyMuPDF splits a few more tokens); x-coordinates agree within 1 pt, so templates are backend-independent (`tests/test_pdf.py`).
  - Identical extraction and validation results on all three documents with both backends.
  - Known-template path, real statement: ~3.5 s (pdfplumber) vs ~110 ms (PyMuPDF).
  - PyMuPDF opens non-PDF files (text, images) as documents; guarded with `is_pdf`.
- **Decision:** Extraction code depends on `bank_parser.pdf` types only. Default backend: **pdfplumber**. PyMuPDF is an optional extra (`uv sync --extra fast`, `BANK_PARSER_PDF_BACKEND=pymupdf`). Unknown-layout inference always uses pdfplumber's table finder for column geometry.
- **Reason:** PyMuPDF's AGPL licence affects a network-served product — an owner decision, not a technical one.
- **Follow-up:** Owner to decide on PyMuPDF (AGPL compliance or commercial licence) vs staying on pdfplumber.

## D-008 — Statement schema v2

- **Date:** 2026-10-03
- **Decision:** Added `bank_address`, `account_holder_address`, header `extra`, `StatementSummary` (printed totals and counts), `Provenance` (document ID, strategy, template ID/version, backend, page count, timestamp) and transaction `extra`. Transaction `amount` and `direction` (DEBIT/CREDIT) are derived from `debit`/`credit`, so they can never disagree. `bank` was renamed `bank_name`; v1 JSON still loads.
- **Reason:** Instructions §2 (required output, extensible, nothing useful thrown away) without duplicating state.

## D-009 — Templates: versioned data, append-only registry

- **Date:** 2026-10-03
- **Decision:** A template is JSON data executed by one engine (no code in templates): bank identity, `layout_id`, `version`, markers, columns (role + x-range), date formats, amount mode, header rules, required checks, provenance, regression status. Stored at `<bank>/<layout>/v<N>.json`; each version is written with exclusive-create, so even two racing workers cannot overwrite one (`tests/test_templates.py`). Committed, reviewed templates live in `template_registry/`; runtime-learned ones in gitignored `data/templates/`, promoted by a human after review.
- **Observed evidence:** The HDFC layout has agent-learned v1 and human-reviewed v2 (adds address rules); the real SBI layout has v1 (agent), v2 (branch address), v3 (formatting) — all versions kept.
- **Rejected alternatives:** Database-stored templates (harder to review and diff); one mutable template per bank (instructions §8).

## D-010 — Row assembly from positioned words ("word_columns"), not ruled-table cells

- **Date:** 2026-10-03
- **Question:** How should transaction rows be assembled?
- **Candidates considered:** (1) pdfplumber `extract_tables()` rows; (2) words assigned to columns whose x-boundaries come from the table's ruled cells. Camelot/tabula not tested — both are ruled-table extractors with the same failure mode as (1), and add Ghostscript/Java dependencies.
- **Observed evidence:** (1) silently dropped a row split across pages (D-002) and needed per-layout filler-row handling (D-005). (2) reproduced the hand-verified SBI ground truth **32/32 rows, 0 field differences**, including the dropped row; HDFC 20 rows; real SBI 81 rows with balance chain and printed-total reconciliation passing.
- **Decision:** Adopt (2). Ruled cells are used only to find column boundaries; content decides rows: a new row starts at a new date in the date column, other lines continue the current row (wrapped dates, multi-line descriptions, page breaks), and a line that does not fit the grid ends the table on that page.
- **Remaining uncertainty:** Layouts with neither ruled cells nor text headers.

## D-011 — Deterministic pipeline; agentic, bounded template learning; tool cost model

- **Date:** 2026-10-03
- **Decision:** Everything on the extraction path is deterministic. The only agentic component is the template agent: a bounded propose → extract → validate → regression → refine loop (limits: 3 candidates, 2 refinements, 300 s, 0 LLM tokens), ending in ACCEPTED (new template version) or HUMAN_REVIEW with reasons. Proposers are pluggable; the default `HeuristicProposer` is deterministic. `LLMProposer` is a disabled slot.
- **Observed evidence:** The heuristic proposer learned all three layouts (two synthetic, one real) in 0.15–1.7 s; no layout so far needed an LLM.
- **Markers:** built only from a fixed vocabulary of label words before colons, so templates cannot contain names, addresses or numbers (`tests/test_templates.py`).

**Tool cost model** (instructions §6–7):

| Candidate | Kind | Local | API / tokens | Per-doc cost | Financial data leaves machine | Latency | Extra service | Lock-in | Improves quality here | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| pdfplumber | Python library | Yes | No | None | No | ~170 ms/page | No | Low (MIT) | Baseline | **Adopted** (default) |
| PyMuPDF | Python library | Yes | No | None (AGPL or paid licence) | No | ~11 ms/page | No | Licence | Same accuracy, 15× faster | Optional; owner decision |
| pdfplumber `extract_tables` rows | Library feature | Yes | No | None | No | Similar | No | Low | No — dropped a row | Used for column geometry only |
| Camelot / tabula | Python library | Yes | No | None | No | Not measured | Ghostscript / Java | Low | Unlikely (ruled-table approach already failed) | Not tested |
| Claude API as template proposer | External model | No | Yes, tokens | Per new layout, not per document | Only a redacted layout summary would be sent | Seconds | API | Moderate | Unproven: heuristics handled every layout so far | Interface only, disabled |
| Local LLM (Ollama, prototype) | Local model | Yes | Local compute | None | No | Seconds | Ollama | Low | Poor numeric fidelity at 4B | Rejected for extraction |
| DocuClipper / Textract / Azure Document Intelligence | External service | No | Yes | Per page | **Yes** | Network round-trip | Vendor | High | Untested; the local path already meets acceptance on all documents | Not tested — needs synthetic/consented documents and approval |
| Claude `pdf` skill | Claude skill | Runs via the model | Tokens | Per use | Yes, to the model | Seconds | The Claude session | — | Development aid, not a runtime component | Not in pipeline |
| Context7 | MCP (remote) | No | Queries only | None | No (only library questions) | — | — | — | Verified pdfplumber `Page.close()` and `password=` | **Used** in development |
| Debugger MCP | MCP | — | — | — | — | — | — | — | Not available in this session; not needed | Not used |
| MCP server exposing `parse_statement` | Future interface | — | — | — | — | Adds a hop | Yes | — | Only if an agent consumes the parser | Phase 6 |

## D-012 — No size cap; bounded resources; content-addressed storage

- **Date:** 2026-10-03
- **Decision:** No application-level size limit (`BANK_PARSER_MAX_UPLOAD_BYTES` exists only as an optional infrastructure guard, unset). Uploads stream in 1 MiB chunks while hashing; pages are parsed one at a time and their caches released (`Page.close()`, verified via Context7); only header pages are retained; extraction and learning run in separate bounded pools (learning in its own process by default). The sha256 of the file is the document ID, so re-uploads are idempotent and there is no shared cache path.
- **Supersedes:** PLAN §10/§18 "file/page size limits" (instructions §1).

## D-013 — Application: FastAPI, SQLite, in-process pools

- **Date:** 2026-10-03
- **Decision:** FastAPI + a single static page (no build step); stdlib `sqlite3` in WAL mode; `ThreadPoolExecutor` for extraction and `ProcessPoolExecutor` for learning. All local and free; no new infrastructure.
- **Rejected for now:** Celery/RQ/Redis (recurring infrastructure; needs approval per PLAN §17), PostgreSQL (no multi-user need yet), Docker (not needed to run; can be added).
- **Known limits:** No authentication (binds to 127.0.0.1); in-flight learning jobs are lost on restart (marked in the DB, not resumed); passwords are not stored, so encrypted documents cannot be re-processed without the user re-entering the password.

---

## Candidates noted, not adopted

- See the tool cost model in D-011. Open owner decisions: PyMuPDF licence (D-007), sample-data PII (D-004), whether to commit the real-statement layout template (it contains labels and geometry only).
