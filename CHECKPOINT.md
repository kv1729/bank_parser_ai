# Checkpoint — how far are we from the goal?

*Updated 2026-10-14. This file answers "how close are we?". `STAGES.md` answers "what are we doing next, and how?". `docs/DECISIONS.md` answers "why?".*

## The goal

Upload any text-based bank statement and get its data back **fast, verified and stored**, with **no human in the loop**:
- Known layouts are extracted by deterministic templates in well under a second.
- **Unknown layouts are still extracted and self-checked** while a local AI agent learns a reusable template for them.
- Every change to the engine or the templates is **proven not to break** any statement that worked before.
- A human is needed only when confidence is low, and the system says so.

## Distance at a glance

| Area | Done | Total | Status |
|---|---|---|---|
| A. Extraction core | 6 | 8 | 🟡 |
| B. Self-check of each extraction | 4 | 6 | 🟡 |
| C. Regression safety (golden set) | 1 | 6 | ⬜ |
| D. AI agent (learning new layouts) | 2 | 8 | ⬜ |
| E. Confidence & autonomy | 0 | 5 | ⬜ |
| F. Intake & secrets | 2 | 5 | 🟡 |
| G. Production readiness | 3 | 7 | 🟡 |
| **Total** | **18** | **45** | **40%** |

✅ done · 🟡 partly · ⬜ not started. The *Stage* column says where each open item is planned.

## A. Extraction core

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| A1 | Text PDFs read fast (pypdfium2, ~18× pdfplumber) | ✅ | D-015 | 8 |
| A2 | Password-protected PDFs | ✅ | `pdf/backend.py`, one password per line | 7 |
| A3 | Rows from positioned words: wrapped dates, multi-line descriptions, page splits | ✅ | D-010, D-018 | 4, 7 |
| A4 | First description line above the date (current SBI layout) | ✅ | D-018 | 7 |
| A5 | Versioned templates, never overwritten | ✅ | D-009 | 4 |
| A6 | Known layout extracted in < 0.5 s | ✅ | 0.41 s for 9 pages (D-015) | 8 |
| A7 | Layouts whose first column is not the date (e.g. `Sr No`), amounts with `₹`/`Rs.` | ⬜ | Second real statement fails (STAGES 7) | 7 |
| A8 | Layouts without ruled grid lines **and** without heading text | ⬜ | The agent's job (D), or Docling (D-016) | 10 |

## B. Self-check of each extraction (no expected answer needed)

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| B1 | Balance chain on every row | ✅ | `core/validation.py` | 2 |
| B2 | Reconciliation with the bank's printed totals and counts | ✅ | D-005 | 4 |
| B3 | Row and header checks (one of debit/credit, dates in period, IFSC) | ✅ | `core/validation.py` | 4 |
| B4 | Result marked verified / needs review | ✅ | `EXTRACTED` vs `NEEDS_REVIEW` | 4 |
| B5 | Checks for **text** fields (descriptions, names), which arithmetic cannot verify | ⬜ | e.g. every row's first line captured, no text left over between rows | 10 |
| B6 | A confidence score per document and per field | ⬜ | Needed before removing the human | 11 |

## C. Regression safety: what worked must keep working

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| C1 | Synthetic samples tested on every push (CI) | ✅ | 175 tests, `.github/workflows/ci.yml` | 6 |
| C2 | **Golden set**: approved statements with their approved output snapshot (private, local) | ⬜ | | 9 |
| C3 | "Approve as golden" in the review screen | ⬜ | | 9 |
| C4 | `regress` command: re-extract every golden statement and compare field by field | ⬜ | | 9 |
| C5 | Gate: no template saved and no engine change accepted unless C4 is identical, or the diff is re-approved | ⬜ | | 9 |
| C6 | Anonymised copies of each golden layout (same geometry, fake values) so CI protects it publicly | ⬜ | | 9 |

## D. AI agent: learning new layouts

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| D1 | Bounded learning loop (propose → extract → self-check → regression → save), never blocking extraction | ✅ | D-011, `test_pipeline.py` | 4 |
| D2 | Recipe-based proposer for ruled tables | ✅ | `learning/proposers.py` | 4 |
| D3 | Local open-source model installed and selected (≤ 3 candidates, ≤ 2 tested) | ⬜ | Ollama present; model to choose (D-019) | 10 |
| D4 | AI proposer: reads the statement and proposes a template | ⬜ | Slot exists, disabled | 10 |
| D5 | AI fallback extraction when no recipe or template fits; status `LEARNING`, then the result appears | ⬜ | | 10 |
| D6 | Every AI result passes the same self-check (B) and regression gate (C) | ⬜ | | 10 |
| D7 | Learns from human corrections: flags and approvals become examples for the agent | ⬜ | | 10 |
| D8 | Agent measured on held-out statements (success rate, time, re-tries) | ⬜ | | 10 |

## E. Confidence & autonomy: removing the human

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| E1 | Agent's proposals agree with human approvals on a measured track record | ⬜ | | 11 |
| E2 | Agent may approve golden snapshots for a layout once E1 holds for it | ⬜ | | 11 |
| E3 | Low confidence routes to a human automatically, and only then | ⬜ | | 11 |
| E4 | Drift detection: a known bank changes its layout → new version learned, old kept | ⬜ | | 11 |
| E5 | Autonomy dashboard: layouts, golden counts, pass rates, human interventions per week | ⬜ | | 11 |

## F. Intake & secrets

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| F1 | Upload page and review screen | ✅ | D-017 | 7 |
| F2 | Content-addressed storage (same file = same document) | ✅ | D-012 | 4 |
| F3 | Passwords in Windows Credential Manager, keyed by document ("Remember password") | ⬜ | Today: `sample_data/.pdf_password`, plain text | 12 |
| F4 | One intake path: watched `inbox/` folder or upload | ⬜ | | 12 |
| F5 | Data at rest encrypted (BitLocker on the data drive) | ⬜ | Owner action | 12 |

## G. Production readiness

| # | Capability | Status | Evidence / where | Stage |
|---|---|---|---|---|
| G1 | No size cap, bounded resources, thread-safe PDF engine | ✅ | D-012, D-018 | 4, 7 |
| G2 | Privacy gate on every commit | ✅ | `.githooks/` | 5 |
| G3 | Runbook and one-click start | ✅ | `docs/RUNBOOK.md` | 7 |
| G4 | Background jobs survive restarts | ⬜ | | 14 |
| G5 | Authentication | ⬜ | | 14 |
| G6 | OCR for scanned statements | ⬜ | | 13 |
| G7 | Deployment (container) and monitoring | ⬜ | | 14 |

## What is holding the agent back

The agentic stage (10) starts after **C (the golden regression gate, Stage 9)**: an agent that changes templates automatically must not be able to silently break statements that already work. The other prerequisites are decided (D-019): a **local open-source model** (free; data never leaves the machine), and human approval first, autonomy later.

**Hardware note:** this laptop runs models on the CPU (16 GB RAM, a 2 GB GPU), so a 3–8 B model takes seconds to minutes per new layout. That is fine for *learning* a template once. A first extraction of an unseen layout through the model will be slower than the template path; a machine with a GPU, or a server, would remove that gap.
