# Stages

Progress tracker for this project (global rule 2). One stage at a time. Each stage has a goal, deliverables and exit criteria. Decisions made inside a stage go to `docs/DECISIONS.md`, not here.

**Now:** Stage 6 — Repository restructure (branch `stage-06-restructure`). **Next:** Stage 7 — Accuracy corpus.

| # | Stage | Status | Started | Finished |
|---|---|---|---|---|
| 1 | Review & plan | Done | 2026-10-02 | 2026-10-02 |
| 2 | Output contract & validation (Phase 0) | Done* | 2026-10-02 | 2026-10-03 |
| 3 | Real-statement experiment | Done | 2026-10-02 | 2026-10-02 |
| 4 | Product architecture | Done | 2026-10-03 | 2026-10-03 |
| 5 | Documentation & developer workflow | Done (owner review pending) | 2026-10-04 | 2026-10-04 |
| 6 | Repository restructure | In progress | 2026-10-04 | — |
| 7 | Accuracy corpus & benchmark | Not started | — | — |
| 8 | Robustness & speed | Not started | — | — |
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

## 6. Repository restructure — In progress

- **Goal:** A production-style layout: `backend/` (src layout) + `frontend/`, docs consolidated, prototype archived, CI.
- **Deliverables:**
  - [x] Files moved with `git mv`
  - [x] Imports and paths updated
  - [x] `legacy/prototype/README.md`
  - [x] CI workflow
  - [x] Docs updated
  - [x] D-014
  - [ ] Diagrams re-rendered against the new paths
  - [ ] Pull request merged
- **Exit criteria:**
  - All tests pass, both locally and without PyMuPDF as in CI.
  - App, benchmark and privacy gate work from the new locations.
  - The PR is reviewed.

## 7. Accuracy corpus & benchmark — Not started

- **Goal:** Measure accuracy on enough statements to make decisions (PLAN §12).
- **Deliverables:**
  - ≥20 statements across ~5 banks, each with labelled expected output kept local-only
  - An accuracy report: fully-correct %, row precision/recall, field accuracy
- **Exit criteria:** A benchmark report exists, and every failure is categorised.
- **Depends on:** the owner supplying or consenting to statements.

## 8. Robustness & speed — Not started

- **Goal:** Close the gaps that Stage 7 exposes.
- **Candidates:**
  - Layouts with neither ruled cells nor text headers
  - A single parsing pass for classification and extraction
  - Keeping the PyMuPDF licence decision (D-007) open until then
- **Exit criteria:** Defined at the start of the stage, from Stage 7 results.

## 9. LLM-assisted template proposer — Not started (conditional)

- **Goal:** Learn layouts that the heuristic proposer cannot.
- **Start only if:** Stage 7/8 shows layouts ending in `HUMAN_REVIEW` that heuristics cannot fix.
- **Exit criteria:** It learns those layouts, sends only redacted layout summaries, and stays within the token budget.

## 10. OCR for scanned PDFs — Not started

- **Goal:** Process image-only pages through the same validation.
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
