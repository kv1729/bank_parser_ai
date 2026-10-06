# Stages

Progress tracker for this project (global rule 2). One stage at a time. Each stage has a goal, deliverables and exit criteria. Decisions made inside a stage go to `docs/DECISIONS.md`, not here.

**Now:** Stage 7 — Accuracy corpus. The review screen (`/review`) is built; next the owner reviews statements in it. Stage 8 is paused: its remaining items wait on Stage 7 results. Open owner items are listed under Stage 7.

| # | Stage | Status | Started | Finished |
|---|---|---|---|---|
| 1 | Review & plan | Done | 2026-10-02 | 2026-10-02 |
| 2 | Output contract & validation (Phase 0) | Done* | 2026-10-02 | 2026-10-03 |
| 3 | Real-statement experiment | Done | 2026-10-02 | 2026-10-02 |
| 4 | Product architecture | Done | 2026-10-03 | 2026-10-03 |
| 5 | Documentation & developer workflow | Done (owner review pending) | 2026-10-04 | 2026-10-04 |
| 6 | Repository restructure | Done | 2026-10-04 | 2026-10-04 |
| 7 | Accuracy corpus & benchmark | In progress | 2026-10-06 | — |
| 8 | Robustness & speed | Paused (speed work done; rest waits on Stage 7) | 2026-10-05 | — |
| 9 | LLM-assisted template proposer (only if Stage 7/8 shows a need) | Not started | — | — |
| 10 | OCR for scanned PDFs | Not started | — | — |
| 11 | Production hardening (auth, deployment, persistent jobs) | Not started | — | — |
| 12 | MCP / agent interface | Not started | — | — |

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

## 7. Accuracy corpus & benchmark — In progress

- **Goal:** Measure accuracy on enough statements to make decisions (PLAN §12).
- **Deliverables:**
  - [x] Review screen (owner request, 2026-10-06; D-017): `/review` lists the PDFs, shows the selected PDF (scrollable) beside the extracted output, with a checkbox per header field, printed total and transaction row to flag wrong outputs, plus remarks. Reviews are stored per extraction in `data/` (gitignored).
  - [x] First owner review (2026-10-06), real SBI statement: amounts and arithmetic correct; the first description line was missing and some pages didn't load. Fixed and verified (D-018).
  - [ ] Owner re-extracts the real statement ("Re-extract with current code") and confirms the descriptions are now complete.
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

## 8. Robustness & speed — Paused (speed work done early at the owner's request; the rest waits on Stage 7)

- **Goal:** Close the gaps that Stage 7 exposes.
- **Done early (owner request, 2026-10-05):**
  - [x] pypdfium2 default backend: ~18× faster than pdfplumber, free licence, output identical (D-015)
  - [x] Free alternatives to paid MCP parsers reviewed; Docling is the candidate to test (D-016)
- **Still to do (scoped by Stage 7 results):**
  - [ ] Layouts with neither ruled cells nor text headers (test Docling here, D-016)
  - [ ] A single parsing pass for classification and extraction
  - [ ] Cross-backend equivalence check on every Stage 7 statement
- **Exit criteria:** Defined at the start of the stage, from Stage 7 results.

## 9. LLM-assisted template proposer — Not started (conditional)

- **Goal:** Learn layouts that the heuristic proposer cannot.
- **Start only if:** Stage 7/8 shows layouts ending in `HUMAN_REVIEW` that heuristics cannot fix.
- **Exit criteria:** It learns those layouts, sends only redacted layout summaries, and stays within the token budget.

## 10. OCR for scanned PDFs — Not started

- **Goal:** Process image-only pages through the same validation. Free candidates: Tesseract / OCRmyPDF vs Docling (D-016).
- **Start only after:** Stage 7 benchmark results on text PDFs are acceptable (PLAN §2).

## 11. Production hardening — Not started

- **Goal:** Make the app safe to run beyond localhost.
- **Deliverables:**
  - Authentication
  - Background jobs that survive restarts
  - Deployment (e.g. Docker)
  - Monitoring
  - Safe logging

## 12. MCP / agent interface — Not started

- **Goal:** Expose `parse_statement` to agents, only if an agent will consume it (PLAN §16).
