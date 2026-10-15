# Decision Log

Lightweight record of meaningful decisions (see `docs/PLAN.md` §22).

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
- **Follow-up:** Owner to decide on PyMuPDF (AGPL compliance or commercial licence) vs staying on pdfplumber. *(2026-10-05: no longer needed — pypdfium2 gives comparable speed under a permissive licence; see D-015.)*

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

## D-014 — Repository structure: backend/ + frontend/ monorepo, src layout

- **Date:** 2026-10-04
- **Question:** Is the flat layout (package, prototype scripts and docs side by side at the root) right for a production project?
- **Candidates considered:**
  1. Keep it flat.
  2. Python src layout in a single project.
  3. `backend/` + `frontend/` monorepo, with a src-layout backend.
- **Decision:** (3), chosen by the owner:
  - **`backend/`** is a self-contained uv project: `src/bank_parser/`, tests, scripts, reviewed templates. Inside the package, `core/` holds the domain (schema, validation), `api/` the HTTP routes, and detection moved into `extraction/`.
  - **`frontend/`** holds the web UI, served by the API from a configurable folder.
  - **`docs/`** collects the plan, brief and decisions; `docs/history/` is the archive.
  - **`legacy/prototype/`** holds the old scripts (moved, not deleted).
  - **`data/`** (runtime) and **`sample_data/`** (private statements) stay at the root, both gitignored.
  - **CI** is added in `.github/workflows/ci.yml`.
- **How:** Everything was moved with `git mv`, so per-file history is kept. Import names stay `bank_parser.*`. The synthetic samples were renamed `sbi_synthetic.pdf` / `hdfc_synthetic.pdf`, which also takes a personal name out of the file tree (the history still has it; D-004).
- **Evidence:** All 139 tests pass after the move; without PyMuPDF, 121 pass and 6 skip, as in CI. The benchmark, the experiments and the privacy gate run from their new locations.
- **Reason:** The src layout stops imports silently resolving to stray root files and makes the package installable. The monorepo split leaves room for a real JavaScript frontend and per-service deployment.
- **Paths in D-001 – D-013** refer to the layout at the time they were written.

## D-015 — Default PDF backend: pypdfium2

- **Date:** 2026-10-05
- **Question:** Can we get PyMuPDF-class speed without PyMuPDF's AGPL licence (D-007)?
- **Candidates considered:**
  - **pypdfium2:** Google's PDFium; Apache-2.0 / BSD-3; already installed as a pdfplumber dependency.
  - **Poppler `pdftotext -bbox`:** GPL; an external binary called as a subprocess.
  - **pypdf:** BSD; no reliable word positions.
  - Only pypdfium2 was tested: Poppler adds a GPL binary and process overhead, and pypdf cannot place words.
- **Experiment:** A new backend (`pdf/pypdfium2_backend.py`). We compared the full extraction output with pdfplumber's on all three statements, on both the known-template and unknown-layout paths, plus the latency benchmark.
- **Observed evidence:**
  - **Raw speed.** Word extraction on the real 9-page statement takes **98 ms** (pdfplumber ~1,500 ms; PyMuPDF ~100 ms) and finds 1,717 words (pdfplumber 1,700; PyMuPDF 1,718).
  - **First run was not identical.** Synthetic SBI gave 3 rows instead of 32, and 3 HDFC rows differed. PDFium writes a hyphen at the end of a line inside a cell as U+FFFE and joins it to the next glyph in reading order, which is often in another column, with no space between them.
  - **Fix.** The backend restores the `-` and ends the word there. It also starts a new word whenever the next glyph moves backwards or changes baseline. Word boxes use PDFium's *loose* (font-height) character boxes so that a printed line is never split into two.
  - **After the fix, output is identical to pdfplumber's** on all three documents and both paths: every transaction field, every header field (including the region-rule bank address), the summary, and the validation status. This is locked in by `test_pypdfium2_output_identical_to_pdfplumber` and a hyphen regression test; 156 tests pass.
  - **Known-template path, measured 2026-10-05 with the machine under load:**

    | Backend | Real 9-page statement | Synthetic SBI | Synthetic HDFC |
    | --- | --- | --- | --- |
    | pdfplumber | 8.0 s (about 3.5 s idle, D-007) | 530 ms | 660 ms |
    | **pypdfium2** | **0.41 s** | **67 ms** | **84 ms** |
    | PyMuPDF | 0.16 s | 37 ms | 30 ms |

    pypdfium2 is about 18× faster than pdfplumber; PyMuPDF is about 2.5× faster again. Rerun with `cd backend && uv run python scripts/benchmark.py`.
- **Decision:** **pypdfium2 is the default backend** (`BANK_PARSER_PDF_BACKEND=pypdfium2`), declared as an explicit dependency. pdfplumber stays as an alternative backend and remains the source of ruled-table geometry for unknown-layout inference and template learning; that work happens once per new layout. PyMuPDF stays optional (`--all-extras`) and is not needed.
- **Why this one:** It is fast, free for commercial use (permissive licence), already in the dependency tree, and verified identical to the previous default. It removes the PyMuPDF licence decision.
- **Rejected alternatives:**
  - PyMuPDF as the default: AGPL.
  - Poppler: GPL binary, subprocess overhead.
- **Remaining uncertainty / follow-up:**
  - The equivalence is proven on three documents. Every new statement in the Stage 7 corpus should also be compared across backends.
  - The per-character Python loop could be vectorised if latency matters later.
  - Unknown-layout inference still pays for pdfplumber's table finder: about 1.4 s on the 9-page statement, once per new layout.

## D-016 — Free alternatives to paid bank-statement MCP servers

- **Date:** 2026-10-05
- **Question:** Are there free tools that would solve the problem better than our local pipeline, now that paid services (Bankstatemently, DocuClipper) are ruled out on cost?
- **Candidates considered (≤3 families):**
  1. **Docling** (IBM; MIT; runs fully offline; also offered as an MCP server, `docling-mcp`). It uses AI layout and table-structure models (DocLayNet, TableFormer), plus OCR.
  2. **Dedicated open-source statement parsers.** For example Monopoly (`monopoly-core`), bankstatementprocessor and pdf_statement_reader.
  3. **Generic local PDF MCP servers** (`mcp-pdf`, pdfplumber-MCP, pdftotext-MCP). These wrap pdfplumber, PyMuPDF, Camelot and Poppler.
- **Assessment (no experiment run):**

  | Candidate | Free / local | Extra cost to us | Helps with | Verdict |
  | --- | --- | --- | --- | --- |
  | Docling (library, or MCP) | Yes / yes, MIT | Heavy install (PyTorch plus model downloads, roughly 1–2 GB); seconds per page on CPU | Layouts with no ruled lines and no text header; **scanned PDFs (OCR)** | **Best free candidate** for the unknown-layout fallback and for Stage 10 OCR. Use it as a Python library, not through MCP |
  | Monopoly | Yes / yes | AGPL-3.0 | Only its supported banks: US, Singapore, Swiss. **No Indian banks** | Rejected: licence, and no coverage of our banks |
  | Other statement parsers | Yes / yes | Each is hard-coded for a few foreign banks | Little | Rejected |
  | Generic PDF MCP servers | Yes / yes | An LLM hop per document if used through MCP | Nothing new: the same libraries we already call directly | Rejected for the pipeline; at most a development convenience |

- **Why no experiment yet:**
  - Docling's model download and memory needs are significant on this machine (a background server was already stopped once for low memory).
  - Our current documents all pass with the local pipeline, so a test would not change today's decision.
  - It should be tested where it can win: on Stage 7 documents that our inference cannot read, and on scans in Stage 10.
- **Decision:** Keep the local deterministic pipeline. Record Docling as the free candidate to test, as a Python library and not through MCP:
  - in **Stage 8**, against any Stage 7 layout that ends in human review;
  - in **Stage 10** for OCR, against Tesseract / OCRmyPDF.
- **Principle (unchanged since D-011):** MCP servers are for AI assistants. In a production backend, call the underlying library directly; routing every document through MCP adds an LLM, tokens, latency and run-to-run variation.


## D-017 — Review screen for owner verification

- **Date:** 2026-10-06
- **Question:** How can the owner verify extractions against the source PDF and record which outputs are wrong?
- **Decision:** A `/review` page (`frontend/review.html`) with three panes:
  1. **PDFs available for review:** the local private folder (`sample_data/`), the synthetic samples, and anything uploaded. Selecting a file that is not yet imported imports it and processes it.
  2. **The PDF itself,** rendered page by page on the server with pypdfium2 (`pdf/render.py`) and scrolled in the browser.
  3. **The extracted output:** a checkbox on every header field, printed total and transaction row, plus a free-text remarks box. Changes auto-save. Clicking a transaction scrolls the PDF to its page, and rows named by a validation issue are marked.
- **Details:**
  - **Server-side rendering, not the browser's PDF viewer.** It works the same everywhere, handles encrypted PDFs without the browser asking for a password, and allows extracted rows to be highlighted on the page later.
  - **Passwords** live in an in-memory vault with a 4-hour limit (`review.PasswordVault`) and are never stored. Imports from a folder use that folder's local `.pdf_password` file; otherwise the page asks for the password (`POST /unlock`).
  - **Reviews** are stored in SQLite (`reviews`), one per (document, extraction): flagged keys (`header.<field>`, `summary.<field>`, `txn.<row_index>`) plus remarks. Reprocessing creates a new extraction, so an old review never silently applies to new output. Keys are validated against the extraction they belong to.
  - **Granularity:** one checkbox per header field and per transaction *row*, not per cell. That's enough to locate an error; the remarks carry the detail.
  - **Safety:**
    - Source files resolve only by listed name, never by path (path traversal is rejected and tested).
    - Statement text is inserted with `textContent` only.
    - Amounts are shown with Indian digit grouping by string manipulation, never via floats.
- **Evidence:**
  - 11 API tests (`tests/test_review.py`): sources, idempotent import, PNG rendering, review round trip, key validation, traversal, password flows.
  - The full suite passes (167).
  - Checked manually in a real browser on synthetic data: flags and remarks auto-save and survive a reload, and clicking a row scrolls to its page.
- **Next:** Reviews feed the Stage 7 accuracy report and become the local-only labelled expected outputs.

## D-018 — Fixes from the owner's first review

- **Date:** 2026-10-06
- **Source:** the owner reviewed the real statement in `/review`. Arithmetic and amounts were correct. Three problems were found.

### Missing first line of descriptions (real SBI layout)

- **Observed:** the first description line ("WDL TFR", "DEP TFR", …) was missing from every row.
- **Cause:** In this layout the first line of the description cell sits just *above* the date line and overlaps it: −1.8 pt with pypdfium2, −0.9 pt with pdfplumber. The row builder started a row only at a date, so that line was attached to the end of the *previous* row. The balance chain cannot detect this because amounts were unaffected, which is why validation passed.
- **Fix (`extraction/table.py`):**
  - Undated lines are held until the next date line arrives.
  - A block of packed lines (gaps ≤ 2.0 pt) whose last line touches or overlaps the date line (gap ≤ 0.5 pt), and which is separated from what precedes it, starts the *new* row.
  - Everything else continues the previous row, as before.
- **Evidence:**
  - **Measured gaps** before a date line: the real statement overlaps (−1.8 / −0.9 pt); synthetic SBI 8.3 / 9.2 pt; HDFC 6.8 / 7.7 pt. So the rule cannot fire on the other layouts.
  - **Real statement after the fix:** 76/81 rows start with WDL/DEP TFR, and 0 rows still end with a stray one. The other 5 are different transaction types (DEBIT…, INTEREST CREDIT, a different deposit type), and their first lines were captured too. Validation still passes, and both backends give identical output.
  - The synthetic SBI ground truth still matches 32/32, and HDFC is unchanged.
  - Two synthetic regression tests reproduce both geometries.

### Review screen: pages missing, server crash

- **Observed:** several pages of the 9-page statement did not show. Switching to another PDF kept showing the first one's output.
- **Cause:** The browser requests several page images at once. They were rendered with pypdfium2 in parallel threads, but **PDFium is not thread-safe**, and the server process died with no traceback (exit code 3). Every later request then failed, including the next document's output. Processing threads use PDFium too, so the same crash could have happened during extraction.
- **Fix:** One process-wide `PDFIUM_LOCK` (`pdf/pdfium_lock.py`) wraps every pypdfium2 call: opening, page parsing, rendering, closing. The rendered image is copied before the lock is released. Template learning runs in its own process and is unaffected.
- **Evidence:**
  - Without the lock, a 12-thread stress run crashes with exit code 3; with it, the run completes.
  - Regression tests cover concurrent render + parse (`test_pdf.py`) and 32 parallel page requests through the API (`test_review.py`).
  - In the browser, a 10-page PDF loads 10/10 pages and the server stays up.

### Review screen hardening

- Each selection carries a token. A slow response for a previously selected PDF can never overwrite the current one.
- Page images retry once, then offer a click-to-retry placeholder.
- Pending edits are saved before the screen switches documents.
- **Re-extract** (`POST /api/documents/{id}/reprocess`, a link in the output pane) runs extraction again with the current code. The result is a new extraction; a saved review stays attached to the extraction it was made on.

## D-019 — Direction: from human review to an autonomous local agent

- **Date:** 2026-10-14 (owner decisions after the second real statement failed layout inference)
- **Goal:** Remove the human from the loop and extract with measurable confidence. The human trains the system first and is phased out per layout as confidence is earned.

**Two different checks** (named so they are not confused):

| | Self-check | Regression |
| --- | --- | --- |
| When | Every new statement, including unknown layouts | Every engine or template change |
| Compares against | The statement itself: balance chain, printed totals and counts, dates, formats | Approved ("golden") snapshots of earlier statements, field by field |
| Proves | Numbers and row order are right; *not* text fields | Nothing that worked before broke |
| Status | Built | Stage 9 |

**Decisions:**
1. **Extraction never waits for learning.** Known layouts and recipe-guessable layouts extract immediately. When no recipe or template fits, the local agent extracts; the document shows `LEARNING` until its self-checked result is stored. Meanwhile the agent learns a reusable template, which must pass the regression gate before it is saved.
2. **The agent may see full statement content.** It runs a **free, open-source model locally** (via Ollama, already installed), so no statement data leaves the machine and no data-processing agreement is needed.
   - If a cloud model is ever used, it needs a data-processing agreement and zero data retention.
   - A BAA is the US health-data (HIPAA) instrument; bank statements in India fall under the DPDP Act 2023.
   - Claude, the development assistant, still sees only masked or aggregate output (global rule 3).
3. **Model:** open-source and free. Up to 3 candidates, up to 2 tested in Stage 10, chosen for this laptop.
   - **Hardware:** CPU inference, 16 GB RAM (about 2.5 GB free when measured), 2 GB GPU. Realistic sizes are 3–8 B parameters, quantised.
   - **Installed:** gemma:2b, qwen:4b, phi3:mini, mistral 7B — an older generation.
   - **Candidates to evaluate:** current small instruct models with permissive licences, e.g. Qwen2.5-7B / 3B-Instruct (Apache-2.0) and Llama-3.2-3B-Instruct (community licence); a vision variant later for OCR.
   - **Expect:** seconds to minutes per new layout on CPU. Acceptable for learning once; a first extraction of an unseen layout is slower than the template path until better hardware is available.
4. **Golden approval:** a human approves first, in the review screen, with zero flags and verified arithmetic. The agent approves later, per layout, after a measured track record (Stage 11; proposed threshold: 10 consecutive statements matching the human with zero corrections).
5. **Order:**
   1. Stage 7: `Sr No`/`₹` fix (rule-based, fast path) and first golden statements.
   2. Stage 9: golden set and regression gate — the prerequisite for any automatic change.
   3. Stage 10: local AI agent.
   4. Stage 11: autonomy.
   5. Stage 12: password vault and intake.
   6. Then OCR, hardening and MCP.
6. **Tracking:** `CHECKPOINT.md` is the goal checklist (capabilities, % done); `STAGES.md` stays the work plan.

## D-020 — More layouts, US data and compliance, agent self-review

- **Date:** 2026-10-14

### Layout fixes (statements 2 and 3, both real)

| Gap | Fix | Where |
| --- | --- | --- |
| First column is a serial number (`Sr No`, `S No.`), so the date is not column 1 | The date column comes from the heading words, otherwise from the first three columns by content. The order tried starts with today's choice, so earlier layouts are unaffected | `extraction/inference.py` |
| Amounts printed with `₹`, `Rs.`, `INR`, `$`, `USD` | Currency markers removed before parsing; sign and parentheses kept | `extraction/parsing.py` |
| Column header printed on page 1 only (statement 2) | The template records `header_on_every_page = False`. On such pages, text above the table is skipped instead of ending the page | `inference.py`, `table.py` |
| Statement lists transactions **newest first** (statement 2) | Balance chain, date order and closing balance are checked in chronological order, whichever way the statement prints them | `core/validation.py` |
| Too few `Label :` markers to recognise the layout (statement 3) | Fallback: runs of statement words on the printed column-header lines. Used only when fewer than 2 label markers exist, so earlier layouts keep identical markers | `learning/markers.py` |

- **Evidence:**
  - Fingerprints (SHA-256 of the full output) of the three statements that already worked are **identical** before and after, on both PDF backends and both paths.
  - Statement 2: 36 rows, 35 verified by the balance chain (the first cannot be: no opening balance printed). The agent learned `boi/layout-23f4f42a/v1`.
  - Statement 3: 18 rows, 17 verified. The agent learned `icici/layout-ec27b806/v1`.
  - Both templates were checked against the 3 other layouts. 185 tests pass, including synthetic regression tests for each fix.
- **Known limitation:** on statement 3 (rows without grid lines, tightly packed), **pdfplumber** merges one description line into the next row; **pypdfium2 (the default) is correct**, confirmed by the PDF's own text order. Changing line grouping could affect older layouts, so it waits for the Stage 9 regression gate.

### US bank data (future) and compliance

*Engineering summary, not legal advice: confirm with counsel before handling real customer data.*

| Jurisdiction | Main obligations relevant to us |
| --- | --- |
| **India (now): DPDP Act 2023** | Lawful purpose and consent; data minimisation; reasonable security safeguards; breach notification to the Data Protection Board and to affected people; deletion when the purpose is served |
| **US: GLBA** (Privacy Rule; FTC Safeguards Rule for non-bank financial service providers) | Written information-security programme; risk assessment; encryption in transit and at rest; MFA and access control; vendor oversight; incident response; notify the FTC of breaches affecting ≥ 500 people |
| **US states** | Breach-notification laws in every state; CCPA/CPRA (partly exempt for GLBA data); NY SHIELD; NYDFS Part 500 if serving NY-regulated entities |
| **PCI DSS** | Only if full card numbers are stored. Avoid by keeping card numbers masked |
| **SOC 2** | Not a law, but usually required by business customers |

- **Engineering consequences (planned):**
  - Stage 12: encryption at rest and a secrets vault.
  - Stage 14: authentication and access control, audit log, retention and deletion policy, breach-response runbook.
  - Ongoing: privacy-gate patterns for US identifiers (SSN, ABA routing, card numbers); keep the AI model local so no vendor receives the data.
- **US format differences:**
  - MM/DD/YYYY dates are ambiguous with DD/MM; the format chooser prefers chronologically consistent readings, and a per-bank locale will be recorded in templates.
  - `$` amounts are now parsed; parentheses mean negative.
  - ABA routing numbers replace IFSC, so header validators become locale-aware.

### Agent self-review (owner requirement)

For every statement the agent builds, it also writes **its own review**:
- what it checked;
- which fields or rows it is unsure of, and why;
- a confidence per field.

The review screen will show that review beside the output. The owner gives feedback on **both** the extraction and the agent's review (agree/disagree per item, plus remarks). The feedback is stored as training material and drives the autonomy decision in Stage 11.

## D-021 — Account details on new layouts; review marks "missing"

- **Date:** 2026-10-14
- **Owner report:** on statements 2 and 3 the account details (holder name, address, branch, period) were not extracted, though statement 1 was fine; and saving a review did not seem to work, so there was no way to say "printed but not extracted".
- **Cause (extraction):**
  - Header rules were written for the SBI/HDFC labels.
  - Statement 2 prints "Account holder name:" and an address beside its label, wrapping one line above and below it.
  - Statement 3 prints the name and address with no labels, a period as "Mon d, yyyy – Mon d, yyyy", and "in INR".
  - Learned templates copy only the generic rules that matched when they were learned, so better generic rules do not reach an existing template.
- **Fix (extraction):**
  - New header rule kind `block`: a labelled value, ended by a wide gap (next column), plus aligned lines above and below whose label column is empty. Measured in label character widths, so it is scale-independent.
  - Generic fallbacks for holder name/address (block), branch (block, trailing comma dropped), month-name periods, currency from "in INR" or `₹`. They run only when earlier rules left a field empty.
  - New template versions, saved beside v1 (never overwritten): `boi/layout-23f4f42a/v2` and `icici/layout-ec27b806/v2` (unlabelled name/address/branch address as page regions).
- **Evidence:**
  - Header fields filled: statement 2 from 6 to 9; statement 3 from 2 to 9. Transactions and totals identical on both backends.
  - Statement 1 and the synthetic samples: output fingerprints identical on both backends and both paths.
  - Values were checked as shape masks only (no content viewed).
  - Opening/closing balances are not printed on either statement; they are left empty rather than derived.
- **Cause (review screen):**
  - The Save bar was positioned below the visible area (`bottom:-80px`), so its "Saved"/"Save failed" message was hidden on long statements.
  - A save error that was not JSON was swallowed.
  - A stale timer re-saved the previous statement on every switch.
  - After a re-extraction, the earlier review was hidden.
- **Fix (review screen):**
  - Per field: **OK / Wrong / Missing**. "Missing" is stored separately (`reviews.missing_json`, an additive column) so the golden set (Stage 9) can tell wrong values from absent ones.
  - The bar is always visible, with clear saved/failed states.
  - Pending edits are saved before switching statements, with a warning when leaving with unsaved edits.
  - After a re-extraction, the earlier review is offered with **Copy that review here**.

### D-021 addendum — owner's second review (2026-10-14)

- **What happened:** after **Re-extract**, statement 2 still had empty holder name and address, and the owner marked both. Statement 3 had no marks.
- **Cause:** the server had been started before the code update. The new v2 templates are read from disk, but the old program did not know the `block` rule kind, logged `unknown_rule_kind` and skipped the rule, so the output was partial with no visible error.
- **Safeguard:** loading a template now fails when it uses a rule kind the code does not know (`HEADER_RULE_KINDS`). Detection then uses an older version this code can run, instead of producing partial output (`test_unknown_header_rule_kind_rejected`).

### D-021 addendum 2 — owner's third review; data access (2026-10-14)

- **Owner report:** statement 2's name and address marked **Missing**; review times should be in IST.
- **Cause:** the review was made on extraction #16, produced by the old code before the restart; it was not re-extracted afterwards.
- **Result:** extraction #20 (current code) has every account field the PDF prints, checked against page 1. Opening/closing balances and a bank address are not printed.
- **Correction:** a labelled block is one wrapped table cell, so its lines are now joined with spaces (a locality name split across two lines stays one name), not commas the bank did not print. Saved as template `boi/layout-23f4f42a/v3`; v1 and v2 kept. Only statement 2's output changed (fingerprints).
- **IST:** the review screen shows save times in India Standard Time; the database keeps UTC.
- **Data access:** the owner allowed Claude to view real statement content for training and debugging. Recorded as an exception in the owner's global rules: this project only, only what a task needs, never committed, logged or sent anywhere.

## D-022 — Golden statements and the non-regression gate (Stage 9)

- **Date:** 2026-10-14
- **Owner:** approved starting Stage 9 alongside the rest of Stage 7.
- **Problem:** nothing stopped a change from silently breaking a statement that worked. The agent checked only that a new template's markers do not take over another layout, and CI covered only the synthetic samples.
- **Decision:**
  - **Golden store:** a golden statement is an owner-approved extraction, stored as an append-only snapshot (`golden_snapshots`, local DB). Re-approval adds a version.
  - **Approval rules:**
    1. The latest extraction.
    2. A saved review with 0 wrong and 0 missing.
    3. No failed validation; warnings must be confirmed.
    4. The PDF can be reopened later: unencrypted, or with a line of `sample_data/.pdf_password`. The password is never stored; a vault comes in Stage 12.
    5. The current code reproduces the reviewed output.
  - **One extraction path:** `extraction/select.py` decides which template extracts a document. The app, `regress` and the agent gate all use it, so they cannot disagree.
  - **What `regress` compares:** every field (header, totals, each transaction field, row count) and the extraction path. A known layout that falls back to the unknown-layout path is a regression even if the output matches. A test found exactly that case: a wrong amount failed the template's balance check, and the fallback reproduced the old output. A newer template version with identical output is allowed and shown.
  - **Gates:** the template agent runs the regression with its candidate added before saving, and refuses on any golden difference. `.githooks/pre-push` runs `regress` before every push. Accepting a difference is human-only, by re-approving in `/review` (D-019).
  - **Privacy:** printed output is shape-masked by default (`--show-values` to see values); reports go to `data/regress/` (gitignored).
- **Evidence:**
  - `tests/test_golden.py`: one changed description is reported at exactly `transactions[5].description` and the other golden stays PASS; the template fallback is caught; the agent gate refuses a hijacking candidate and `registry.save` is never called.
  - 204 tests pass (180 + 10 skipped without PyMuPDF, as in CI).
  - Fingerprints of all real and synthetic outputs are identical after moving the selection logic.
- **Owner approval (2026-10-14):** statements 1–3 approved as golden v1. `regress --backends both`: 3 × PASS on the gate backend. pdfplumber differs on statement 3 only: each description's last line moves to the next row, the known line-assignment limitation; it is reported, not gated.
- **Anonymised layout copies (C6), built 2026-10-14:**
  - `scripts/anonymise_layout.py` records which word the table engine puts in which row and column, then:
    - keeps every position and the label vocabulary: a fixed list plus every word the template's markers, column labels and header rules rely on;
    - replaces every other word with a consistent same-shape fake (IFSC keeps its bank code; e-mails become `@example.com`);
    - regenerates debits and credits, recomputes balances, opening/closing balances and printed totals;
    - shifts years by −8.
  - Numbers of up to 3 digits stay only inside transaction rows, next to a month name or page label, or as printed counts.
  - **It refuses to write when:** any real word of 3+ characters survives, a header value appears, or the copy's output differs in row count, filled header fields, debit/credit/balance pattern or validation status. The privacy gate also passes on the files.
  - **Evidence:** 3 copies (SBI 9 pages / 81 rows PASS; BOI 36 rows WARN; ICICI 18 rows WARN); `tests/test_layout_fixtures.py` passes. Disabling the lead-line rule (D-018) makes the SBI and ICICI copies fail at the affected descriptions.
  - **Templates:** each copy carries its layout's template (labels and geometry only, provenance ids removed). This partly settles the open item on committing real-layout templates.
  - **Owner approval (2026-10-14):** checked the previews and approved committing both the copies and their templates.
- **Design as planned before building C6:**
  - **Anonymised layout copies for CI (C6):** word-layout JSON fixtures (positions kept; every non-label token replaced with a fake of the same shape; amounts regenerated so the balance chain holds), served by a test double. The owner checks them before commit; at most 2 experiments per layout (PLAN §6).

## D-023 — Local model for the template agent: qwen3.5:9b, labelling only (Stage 10.1–10.2)

- **Date:** 2026-10-15
- **Owner:** approved starting Stage 10 with a **local** model (not a hosted API: privacy, free, repeatable, offline) and the plan for 10.1–10.2.
- **Problem:** the template agent learned only layouts with a ruled table. Tables without ruled lines, with unfamiliar headings, or with no heading text at all (current SBI prints only "Balance") ended in human review.
- **Candidates (PLAN §6: ≤ 3, ≤ 2 tested):**
  - Shortlisted: qwen3.5:4b and qwen3.5:9b (both Apache-2.0), phi4-mini (MIT) in reserve. Licences were read from each model's Ollama licence file.
  - Excluded: gemma3 and llama3.2 (licences not open source); gpt-oss:20b (13.8 GB, too large for 16 GB RAM).
- **Experiment 1 — the model reads the whole page** (numbered lines with x positions) and returns heading lines, last row and column positions:
  - 4b: 0 of 5 cases usable, 85–180 s per answer.
  - Lesson: locating things by line number and position is geometry, which small models do badly and code does well.
- **Experiment 2 — code finds the table, the model only labels it.**
  - **Code** (`learning/llm_view.py`):
    - transaction lines: a date among the first three words, plus a money amount;
    - the 1–3 heading lines above them (description lines touching the first date line are skipped, D-018);
    - columns: heading words grouped by gaps, or the rows' own gaps when there is too little heading text;
    - boundaries: between neighbouring headings, where the fewest table words cross;
    - the date format.
  - **The model** sees each column's heading and three real sample values, with placeholders skipped and taken from up to 4 pages. It answers with one role per column, and nothing else.
  - **Amount mode** follows from the roles in code. Asking the model for it caused one of its errors.
  - **Ollama call:** JSON schema with exactly one role per column, `temperature 0`, fixed seed, answer capped at 200 tokens, context 2048 tokens.
    - The cap is needed: without it, the 4b once generated 2,283 tokens of repeated list items.
    - The small context keeps memory down.
- **Evidence (`experiments/llm_choice.py`, 10 cases, each answered twice):**
  - **Cases:** the 3 golden statements; the BOI and ICICI anonymised copies; the same two with their headings reworded (e.g. "Withdrawals", "Closing Bal", "Narration Details"); the SBI copy (no heading text); the 2 synthetic PDFs.

  | Model | Base cases identical | Hard cases identical | Repeatable | Typical answer | Memory |
  |---|---|---|---|---|---|
  | qwen3.5:4b | 3 / 7 | 0 / 3 | yes | 15 s | 3.3 GB |
  | **qwen3.5:9b** | **7 / 7** | **3 / 3** | yes | 12 s (first 46 s, loading) | 6.1 GB |

  - "Identical" means every transaction's dates, description, reference, debit, credit and balance equal the approved output. The serial-number field differs on the last row of BOI and ICICI: a footer word sits 1.5 pt from the column edge, which the ruled template places differently. Money and text fields are unaffected.
  - With familiar heading words alone, no model is needed: 6 of 10 identical. This path is now part of the heuristic proposer.
  - **Hold-out, end to end** (`experiments/llm_holdout.py`): the agent with **only** the local-model proposer and an empty template registry learned each of the 3 golden statements on the first attempt, with all checks applied. Each took 11–13 s and 444–521 tokens, and every transaction was identical to the golden output. `regress` stays 3 × PASS.
- **Decision:**
  - **Model:** `qwen3.5:9b` is the default (`BANK_PARSER_LLM_MODEL`), with a token budget of 4,000 per learning job (`BANK_PARSER_LLM_MAX_TOKENS`; 0 = off).
  - **When the model runs:** only when Ollama runs locally and the model is installed, and only after the heuristic proposer fails.
  - **Proposer order:**
    1. ruled table;
    2. unruled table labelled by familiar heading words;
    3. local model.
  - **Checks:** a model's labels must pass `_check_content`/`_check_description`, then the agent's arithmetic, marker and golden gates.
  - **Swap:** when the balance chain fails and both debit and credit are labelled, they are swapped once (the balance decides) before the model is asked again. Without headings, the samples cannot tell money in from money out.
- **Safety findings:**
  - **Wrong labels can pass the arithmetic:** a 4b labelling with the description column called "reference" passed validation (PASS) with wrong text. New content checks reject it:
    - date columns must hold dates;
    - money columns must hold amounts;
    - a description column is required and must hold the longest text over whole rows;
    - the balance column must not be empty.

    This is a first part of CHECKPOINT B5.
  - **Memory:** on this 16 GB laptop the 9b leaves little free memory (one benchmark run was stopped by low memory). It runs only for layouts the rules cannot learn, so rarely. If memory becomes a problem, turn it off or close other apps.
- **Rule change (owner-approved with the plan):** only a local model (Ollama on 127.0.0.1; `llm/ollama.py` refuses any other host) may see statement content, never a hosted LLM. Statement text goes to it as fenced data; its answer is schema-checked, never executed. `CLAUDE.md` is updated accordingly.

### D-023 addendum — Stage 10.3: `LEARNING`, then the checked result (2026-10-15)

- **Problem:**
  - For an unruled layout, Workflow 1 stored no result and said `NEEDS_REVIEW` while the agent was still learning.
  - Learning started *before* Workflow 1 stored its result. A fast learner's re-extraction could therefore be overtaken by the older first attempt.
- **Decision:**
  - **Workflow 1 reads unruled tables too,** with familiar heading words (`extract_unruled`, the 10.2 code path). It **never calls the model**, so extraction stays fast and deterministic.
  - **New status `LEARNING`:** shown when the first result is not verified (or there is none) and the agent has started. When the agent accepts a template, the statement is re-extracted with it (the existing `submit_reextract`). Otherwise the status becomes `NEEDS_REVIEW` with the agent's reason.
  - **Order:** store the first result → set the status → start learning.
  - **One learning job per statement:**
    - an upload, a review-list import or **Re-extract** during `PROCESSING`/`LEARNING` does nothing (409 for re-extract);
    - a learner crash closes its job as `FAILED`;
    - at app start, work interrupted by a restart is marked `NEEDS_REVIEW` ("interrupted") so it can be reprocessed (jobs do not survive restarts, CHECKPOINT G4). Only status fields change.
  - `BANK_PARSER_LEARNING_PROPOSERS` (`heuristic,llm` by default; `llm` alone for evaluation).
- **Evidence:**
  - **Pipeline tests:** `LEARNING` then the learned result; an instant learner is not overtaken; a failed learner gives `NEEDS_REVIEW` with its reason; a verified first attempt stays `EXTRACTED`; a re-upload while learning starts no second job; interrupted work is marked.
  - **Workflow 1** reads the BOI and ICICI anonymised copies without ruled lines identically, and leaves the SBI copy (no heading words) to the agent.
  - **End to end** (`experiments/pipeline_holdout.py`): each golden statement went through the real pipeline with no templates and the agent limited to the local model.
    - All 3 were learned by the model in 11–14 s, re-extracted with the learned template, and are identical to golden.
    - SBI: `EXTRACTED` at once, learned in the background. BOI and ICICI: `LEARNING`, then `NEEDS_REVIEW`. They print no opening balance, the same warning their approved templates give today.

### D-023 correction — dates were not compared in the 10.1–10.3 evidence (2026-10-15)

- **What went wrong:** the comparisons behind "identical" used the key `date`, but transactions store `txn_date`. Transaction dates were therefore not compared, in `experiments/llm_choice.py`, `llm_holdout.py`, `pipeline_holdout.py` and the "core" tests in `tests/test_llm_proposer.py`. Money, descriptions, references and value dates were compared correctly.
- **Fixed and re-verified with dates included:**
  - the deterministic check: all 10 benchmark cases are identical with the correct roles, and the 9b's roles equalled them on all 10;
  - `llm_holdout.py` and `pipeline_holdout.py`: 3/3 identical to golden;
  - the tests pass.
- **Conclusions unchanged.**

## D-024 — The system's self-review of each statement, and the owner's verdicts on it (Stage 10.4)

- **Date:** 2026-10-15
- **Owner:** approved the plan (requirement from D-020: the agent reviews its own work, and the owner reviews that review).
- **Problem:** the review screen showed the output and the validation checks, but not *why* a value deserves trust, which values no check covers (descriptions, names), or how the layout was learned. Stage 11 (autonomy) needs a record of where the system's confidence matched the owner.
- **Decision:**
  - **Deterministic, not written by the model.** `self_review.build_self_review` uses only:
    - the validation report;
    - which rule found each account field (`apply_header_rules(..., sources)`, recorded in `ExtractionResult.field_sources`);
    - the template's provenance;
    - the agent job.

    It cannot claim a check that never ran, and it costs nothing. It uses field names, counts, row numbers and check codes, never values or issue messages, which can contain amounts.
  - **Contents:**
    - summary;
    - **what I checked:** balance chain on N of M rows, printed figures matched, dates in order and inside the period;
    - **what I'm unsure of:** each item with an id and a field;
    - **confidence per field:**
      - `high`: an independent check confirms it;
      - `medium`: a rule found it, nothing independent confirms it;
      - `low`: a check questioned it;
      - `missing`.
    - **how the template was made:** rules, heading words or the local model with the model name; debit/credit decided by the balance chain.
  - **Storage:** additive columns `extractions.self_review_json` (written when the extraction is stored; older ones are computed on request, with field sources "not recorded") and `reviews.agent_feedback_json` (the owner's Agree/Disagree plus an optional note per item; only listed item ids accepted; saving a review without feedback keeps the stored verdicts).
  - **Calibration:** `GET /review` compares the owner's marks with the confidence ratings: fields marked wrong or missing that were rated high, and rows marked wrong that were not among the unsure items. Computed, not stored. This is the record Stage 11 builds on.
- **Evidence:**
  - `tests/test_self_review.py`:
    - money high and text medium on a verified statement;
    - no statement value appears in the self-review;
    - row 1 flagged when no opening balance is printed;
    - a broken balance rates that row low;
    - an unlabelled-position field is listed with its reason;
    - a model-learned template says so;
    - calibration counts;
    - the feedback round trip, including refusals.
  - **On the 3 real statements:**
    - money verified on 17/18, 81/81 and 35/36 rows (row 1 of ICICI and BOI has no opening balance);
    - SBI also matches all six printed figures;
    - the unsure items name the descriptions and the account fields read by position or by a general rule.
  - `regress` 3 × PASS: the output is unchanged.

### Code review of Stage 10.1–10.4 (2026-10-15, after PRs #13 and #14 were merged)

- **Findings, all fixed on `stage-10-review-fixes`, each with a regression test:**
  1. **A Workflow 1 crash left the statement `PROCESSING` forever.** Since 10.3, uploads and re-extraction are refused while `PROCESSING`, so the statement was blocked until a restart. Now `process` ends as `FAILED`, with the error type only (never its text, which may contain content).
  2. **An accepted template that could not be loaded, or a re-extraction that crashed, left the statement `LEARNING` forever.** Now it becomes `NEEDS_REVIEW` with the reason.
  3. **On newest-first statements (BOI), the self-review pointed at row 1** as the row without an opening balance. It is the oldest row, which is the last one printed. The validator's own issue still says row 0; its codes are unchanged, so golden snapshots are unaffected.
  4. **Typing a note on a self-review item silently recorded "Agree".** The note is now enabled only after a verdict is chosen.
  5. **The self-review could show learning details from an unrelated earlier job** of the same statement. It now uses the job only when that job made the template.
- **Checked and fine:**
  - the local-only check on the model server address (`user@host`, `host.evil` and IPv6 forms are refused correctly);
  - no statement values in the self-review or in error messages;
  - additive database migrations only;
  - the heuristic proposer stays ahead of the model;
  - golden snapshots unchanged (`regress` 3 × PASS).
