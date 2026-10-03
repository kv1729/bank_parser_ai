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
  - Timing (median of 10 runs): open 2 ms, layout + table finding **~1,390 ms** (~200 ms/page), normalize 1 ms, validation 0.1 ms; total ~1.4 s. Table finding dominates — pages carry 400+ ruling lines.
- **Decision:** Keep pdfplumber tables as the Phase 1 baseline for ruled SBI layouts. Bank/layout detection must use geometry and labels (cell-rectangle column boundaries, "STATEMENT OF ACCOUNT", "Statement Summary") rather than header text.
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

---

## Candidates noted, not adopted

- None new during Phase 0. Table extraction vs word-position column extraction remains the first Phase 1 decision (≤2 experiments, on the two samples).
