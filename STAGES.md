# Stages

Progress tracker for this project (global rule 2). One stage at a time. Each stage has a goal, deliverables and exit criteria. Decisions made inside a stage go to `docs/DECISIONS.md`, not here.

**Now:** Stage 9 (approved 2026-10-14 to run alongside the rest of Stage 7) — golden store, approval, `regress` and both gates are built, and the owner approved statements 1–3 as golden (2026-10-14; `regress`: 3 × PASS). Remaining: anonymised layout copies for CI (C6). Stage 7 continues with more statements. **Next:** Stage 10 (local AI agent). How far from the goal: see `CHECKPOINT.md`.

| # | Stage | Status | Started | Finished |
|---|---|---|---|---|
| 1 | Review & plan | Done | 2026-10-02 | 2026-10-02 |
| 2 | Output contract & validation (Phase 0) | Done* | 2026-10-02 | 2026-10-03 |
| 3 | Real-statement experiment | Done | 2026-10-02 | 2026-10-02 |
| 4 | Product architecture | Done | 2026-10-03 | 2026-10-03 |
| 5 | Documentation & developer workflow | Done (owner review pending) | 2026-10-04 | 2026-10-04 |
| 6 | Repository restructure | Done | 2026-10-04 | 2026-10-04 |
| 7 | Accuracy corpus, review & first golden statements | In progress | 2026-10-06 | — |
| 8 | Speed | Done (remaining robustness items moved to 7 and 10) | 2026-10-05 | 2026-10-14 |
| 9 | Golden set & non-regression gate | In progress (alongside 7, approved) | 2026-10-14 | — |
| 10 | Local AI agent (open-source model), trained with human review | Not started | — | — |
| 11 | Confidence & autonomy (remove the human from the loop) | Not started | — | — |
| 12 | Password vault & intake | Not started | — | — |
| 13 | OCR for scanned PDFs | Not started | — | — |
| 14 | Production hardening | Not started | — | — |
| 15 | MCP / agent interface | Not started | — | — |

\* One open item carried forward: sample-data PII decision (docs/DECISIONS.md D-004).

---

## 1. Review & plan — Done

- **Goal:** Understand the prototype and agree a plan.
- **Deliverables:**
  - `docs/ARCHITECTURE_REVIEW.md`
  - `docs/PLAN.md` (was `Bank_Statement_Parser_PLAN.md`)
- **Exit criteria:** The plan was accepted by the owner.

## 2. Output contract & validation (Phase 0) — Done*

- **Goal:** Define what correct output is before extracting anything.
- **Deliverables:**
  - Typed schema with `Decimal` money
  - Balance-chain validator
  - Hand-verified SBI ground truth (local-only)
  - pytest suite
- **Exit criteria:** Changing any single amount on any row fails validation at that row (`tests/test_balance_chain.py`).
- **Open:** The sample-data PII decision (D-004).

## 3. Real-statement experiment — Done

- **Goal:** Test the approach on a real, password-protected statement.
- **Deliverables:** `experiments/real_sbi_statement.py`, D-005.
- **Exit criteria:** Balance chain passes on all 81 rows, and the bank's printed totals reconcile.

## 4. Product architecture — Done

- **Goal:** Implement the product brief (`docs/PRODUCT_BRIEF.md`), where an unknown layout is extracted immediately while a template is learned in parallel.
- **Deliverables:**
  - PDF backends
  - Versioned template registry
  - `word_columns` engine
  - Unknown-layout inference
  - Bounded template agent
  - Pipeline with separate worker pools
  - SQLite storage and the FastAPI app
  - Benchmark script
  - D-007 – D-013
- **Exit criteria (all met; 139 tests pass):**
  - Extraction completes while learning is blocked (`tests/test_pipeline.py`).
  - Templates are never overwritten (`tests/test_templates.py`).
  - All three documents are verified or reconcile.

## 5. Documentation & developer workflow — Done (owner review pending)

- **Goal:** Anyone can see how the project works, what is used where, and what state it is in.
- **Deliverables:**
  - [x] `STAGES.md` (this file)
  - [x] `docs/PROJECT_GUIDE.md`
  - [x] archify diagrams in `docs/diagrams/`
  - [x] Global stage rule
  - [x] Privacy pre-commit hooks (`.githooks/`, `scripts/privacy_check.py`)
  - [x] Project permission allowlist (`.claude/settings.json`)
  - [x] Working routine in `CLAUDE.md` (session start/end, branch per stage)
  - [ ] Owner review of the guide
  - [x] Repository structure decision (owner chose backend/ + frontend/; carried out in Stage 6)
- **Exit criteria:** The owner confirms the guide answers "how does it work, what is used where, what is the status".

## 6. Repository restructure — Done

- **Goal:** A production-style layout: `backend/` (src layout) + `frontend/`, docs consolidated, prototype archived, CI.
- **Deliverables:**
  - [x] Files moved with `git mv`
  - [x] Imports and paths updated
  - [x] `legacy/prototype/README.md`
  - [x] CI workflow
  - [x] Docs updated
  - [x] D-014
  - [x] Diagrams re-rendered against the new paths (pinned to f2579cd)
  - [x] Pull request merged (#4); CI green on `main`
- **Exit criteria:**
  - All tests pass, both locally and without PyMuPDF as in CI.
  - App, benchmark and privacy gate work from the new locations.
  - The PR is reviewed.

## 7. Accuracy corpus, review & first golden statements — In progress

- **Goal:** Measure accuracy on enough statements to make decisions (PLAN §12).
- **Deliverables:**
  - [x] Review screen (owner request, 2026-10-06; D-017): `/review` lists the PDFs, shows the selected PDF (scrollable) beside the extracted output, with a checkbox per header field, printed total and transaction row to flag wrong outputs, plus remarks. Reviews are stored per extraction in `data/` (gitignored).
  - [x] First owner review (2026-10-06), real SBI statement: amounts and arithmetic correct; the first description line was missing and some pages didn't load. Fixed and verified (D-018).
  - [x] Runbook (`docs/RUNBOOK.md`) and one-click launcher (`start-app.cmd`) after the owner's start-up failed (2026-10-07): starting from the repository root used the stale root `.venv` → `ModuleNotFoundError`.
  - [x] Real statement re-extracted with current code (extraction #8, 2026-10-07): 76/81 rows start with WDL/DEP TFR, none trailing; validation PASS. Owner compared one row against a direct reading and approved the output.
  - [x] CSV export of stored results (`backend/scripts/export_extraction.py`, runbook §4a).
  - [x] `sample_data/.pdf_password` holds one password per line (2026-10-14): review import, re-extract, privacy gate, sensitive-string refresh, tests and benchmark try each line.
  - [x] Second real statement (`dd6efc05…`): serial-number first column, `₹` amounts, header on page 1 only, newest-first order. Fixed (D-020): 35/36 rows verified; agent learned `boi/layout-23f4f42a/v1`. Earlier outputs identical (fingerprints).
  - [x] Third real statement (`171b6c9d…`): serial-number first column, header-only grid, few labels. Extracted, 17/18 rows verified; agent learned `icici/layout-ec27b806/v1` (heading markers, D-020).
  - [x] Owner's first look at statements 2 and 3 (2026-10-14): account details (name, address, branch, period) were not extracted, and saving a review seemed not to work. Fixed (D-021): labelled-block header rule, month-name periods, currency; templates `boi/layout-23f4f42a/v2` and `icici/layout-ec27b806/v2` (9 header fields each, was 6 and 2; transactions identical). Review screen: per field **OK / Wrong / Missing**, Save bar always visible with a clear saved/failed message, edits kept when switching statements, earlier review offered after re-extraction. Statement 1 and synthetic outputs identical (fingerprints).
  - [x] Owner re-reviewed after **Re-extract** (2026-10-14): statement 3 no marks; statement 2 marked holder name and address as printed-but-empty. Cause: the app was still running pre-fix code, which could not run the new `block` rule and silently skipped it. The values extract correctly with current code. Safeguard: templates with a rule kind the code does not know are now rejected at load, so old code falls back to an older version instead of giving partial output.
  - [x] Owner restarted the app and marked statement 2's name and address **Missing** (2026-10-14), on extraction #16, made by the old code before the restart. Re-extracted with current code (#20, template `boi/layout-23f4f42a/v3`): name, address, account number, IFSC, branch, currency and period match the PDF (checked against page 1 with the owner's permission). v3 joins a wrapped cell with spaces instead of adding commas. Review times now shown in IST.
  - [ ] Owner confirms statement 2 extraction #20 in `/review` (do **not** copy the old review: its Missing marks are fixed). Opening/closing balances are not printed on statements 2 and 3, so their first row stays unverifiable.
  - [x] Owner approved the 3 real statements as the first golden set (2026-10-14, Stage 9).
  - [ ] Owner reviews further statements in `/review` (flags + remarks).
  - [ ] ≥20 statements across ~5 banks, each reviewed (the review becomes its labelled expected output, kept local-only).
  - [ ] An accuracy report built from the reviews: fully-correct %, row precision/recall, field accuracy.
- **Exit criteria:** A benchmark report exists, and every failure is categorised.
- **Depends on:** the owner supplying or consenting to statements.
- **Open owner items before or during this stage:**
  - [ ] Review `docs/PROJECT_GUIDE.md` (closes Stage 5).
  - [x] ~~PyMuPDF licence (D-007)~~ — no longer needed: pypdfium2 is the free fast default (D-015).
  - [ ] Commit the real-layout template from `data/templates/`? It holds labels and geometry only.
  - [ ] Sample-data PII (D-004): keep, replace, or rewrite history.
  - [ ] Delete the unused local `.venv/` and `venv/` at the repository root, and merged branches.
  - [x] Claude may view real statement content for training/debugging (owner, 2026-10-14). Recorded as rule 5 in `~/.claude/CLAUDE.md`: this project only, view only what a task needs, never commit, log or send it anywhere.
  - [ ] Approve running Stage 9 alongside the rest of Stage 7 (global rule 2: one stage at a time unless approved).

## 8. Speed — Done (2026-10-14; remaining robustness items moved)

- **Delivered (owner request, 2026-10-05):**
  - [x] pypdfium2 default backend: ~18× faster than pdfplumber, free licence, output identical (D-015)
  - [x] Free alternatives to paid MCP parsers reviewed; Docling is a candidate (D-016)
- **Moved (2026-10-14, D-019):**
  - Layouts with a non-date first column, currency symbols → Stage 7.
  - Layouts with neither ruled cells nor heading text → Stage 10 (agent; Docling as a candidate).
  - Single parsing pass; cross-backend check on every new statement → Stage 9 (part of the regression run).

## 9. Golden set & non-regression gate — In progress

- **Goal:** Nothing that worked before can break: every engine or template change is checked against every approved statement.
- **Deliverables:**
  - [x] Golden store (local, private; D-022): table `golden_snapshots` in `data/bank_parser.db`, append-only versions with the approved output, workflow, template, backend, engine commit, acknowledged warnings and password source (never the password).
  - [x] "Approve as golden" in `/review`: only the latest extraction, with a saved review that has 0 wrong and 0 missing, no failed validation (warnings must be confirmed), a PDF that can be reopened (unencrypted, or a line of `sample_data/.pdf_password`), and output the current code reproduces. Re-approval adds a version. Golden badge and field-by-field differences on screen.
  - [x] `regress` command (`backend/scripts/regress.py`): re-extracts every golden statement exactly as the app does (shared `extraction/select.py`) and compares field by field, plus the extraction path (a template that stops working is caught even when the fallback output matches). Masked values by default; `--show-values`; `--backends both`. Report in `data/regress/`.
  - [x] Gates: the template agent cannot save a template that changes any golden output (`golden_gate`); `.githooks/pre-push` runs `regress` before every push (skips where no golden set exists). Synthetic samples stay covered by CI tests.
  - [ ] Anonymised copies of each golden layout (same geometry, fake values) for CI: word-layout JSON fixtures, owner checks them before commit (plan in D-022).
- **Exit criteria:**
  - Changing one field of the engine output makes `regress` fail on the right statement and field.
  - The agent cannot save a template that changes any golden output.
  - The 3 real statements are golden.
- **Evidence so far:** owner approved statements 1–3 (golden v1 each); `regress --backends both`: 3 × PASS on pypdfium2 in 35–206 ms. pdfplumber (informational) differs only on statement 3, where each description's last line moves to the next row (the known limitation, Stage 8). `tests/test_golden.py` (approval rules, one changed description → DIFF at exactly `transactions[5].description`, template fallback caught, agent gate refuses and never saves); 204 tests pass; fingerprints of all real and synthetic outputs identical after the refactor.

## 10. Local AI agent (open-source model), trained with human review — Not started

- **Goal:** Unknown layouts are extracted without a human: a local model reads the statement, proposes a template and, when no recipe fits, the extraction itself; everything passes the self-check and the regression gate.
- **Deliverables:**
  - [ ] Model choice: ≤ 3 free open-source candidates, ≤ 2 tested on this laptop (CPU) for accuracy, time and memory (D-019).
  - [ ] AI template proposer (full statement content allowed: the model runs locally and nothing leaves the machine).
  - [ ] AI fallback extraction: status `LEARNING`, then the self-checked result appears; known layouts stay on the fast path.
  - [ ] Text checks beyond arithmetic (CHECKPOINT B5).
  - [ ] Agent self-review per statement (what it checked, what it is unsure of, confidence per field), shown in the review screen; the owner gives feedback on the agent's review as well as the output (D-020).
  - [ ] Locale-aware formats and validators for US statements (CHECKPOINT H6).
  - [ ] Human flags and approvals stored as training examples for the agent.
  - [ ] Evaluation on held-out statements: success rate, time, re-tries.
- **Exit criteria:** Each statement that fails today's recipes (e.g. the `Sr No` layout, before its rule-based fix) is extracted and verified by the agent alone, and the golden set stays identical.
- **Privacy:** The agent may see full content only because it is local. Claude (the development assistant) still sees only masked or aggregate output (global rule 3).

## 11. Confidence & autonomy — Not started

- **Goal:** Remove the human from the loop wherever the system has earned it.
- **Deliverables:** Confidence score per document and field; agent auto-approval of golden snapshots per layout after a track record; automatic routing of low-confidence cases to a human; layout-drift detection; autonomy dashboard.
- **Exit criteria (to be confirmed with the owner):** For a layout, the agent's results matched the human on ≥ 10 consecutive statements with zero corrections → that layout runs without human approval.

## 12. Password vault & intake — Not started

- **Goal:** Statements and their passwords come in one way and are found again automatically.
- **Deliverables:** Passwords in Windows Credential Manager (`keyring`), keyed by document fingerprint, with a "Remember password" option; per-bank password profiles; one-time import from `sample_data/.pdf_password`; watched `inbox/` folder; BitLocker check; privacy-gate patterns for US identifiers (CHECKPOINT H5).

## 13. OCR for scanned PDFs — Not started

- **Goal:** Process image-only pages through the same checks. Free candidates: Tesseract / OCRmyPDF vs Docling (D-016), or a local vision model from Stage 10.

## 14. Production hardening — Not started

- **Goal:** Make the app safe to run beyond localhost.
- **Deliverables:** Authentication; background jobs that survive restarts; deployment (container); monitoring; safe logging; compliance controls from D-020: retention/deletion policy, audit log, breach-response runbook (CHECKPOINT H2–H4).

## 15. MCP / agent interface — Not started

- **Goal:** Expose `parse_statement` to other agents, only if one will consume it (PLAN §16).
