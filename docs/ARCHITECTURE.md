# Bank Statement Parser — Architecture

*As of 2026-10-05. Evidence for every choice is in [`DECISIONS.md`](DECISIONS.md) (D-007 – D-016). Repository layout: see the root `README.md`.*

A user uploads a text-based bank statement PDF; the system returns validated structured data: account/statement header, every transaction, and a validation report. Unknown layouts are extracted **immediately**; a separate, bounded template-learning workflow turns each new layout into a reusable deterministic template **in parallel**, never blocking extraction.

## Flow

```text
UPLOAD (streamed, chunked)
  │
  ▼
Store: sha256 = document ID ── same file again → same document (idempotent)
  │
  ▼
Classify: TEXT / MIXED / SCANNED / password-protected / malformed
  │            SCANNED → NEEDS_OCR (later phase)     encrypted, no password → NEEDS_PASSWORD
  ▼
Detect layout: markers of every approved template vs. text of pages 1, 2 and last
  │
  ├── known layout ───────────────► Template extraction (word_columns engine)
  │                                    │
  └── unknown layout                   │
        ├─► Workflow 2 (background pool, never awaited)
        │     Template agent: propose → extract → validate → regression → save vN | HUMAN_REVIEW
        │     on ACCEPTED → re-extract this document with the new template
        │
        └─► Workflow 1 (now): infer layout → extract ──┐
                                                       ▼
                                          Normalize (Decimal, ISO dates, direction)
                                                       ▼
                         Validate: balance chain · reconciliation vs printed totals · rows · header
                                                       ▼
                         Persist (SQLite) → EXTRACTED (verified) | NEEDS_REVIEW
                                                       ▼
                                                 Display in app
```

## Deterministic vs agentic work

| Step | Kind | Why |
| --- | --- | --- |
| Upload, hashing, storage | Deterministic | Pure I/O |
| PDF classification, password handling | Deterministic | Text-layer check per page |
| Layout detection | Deterministic | Whole-phrase label markers |
| Known-template extraction | Deterministic | Template is data, executed by one engine |
| Unknown-layout extraction (Workflow 1) | Deterministic | Geometry + content statistics; the balance chain picks debit vs credit |
| Validation | Deterministic | Arithmetic and format invariants |
| Persistence | Deterministic | SQLite |
| **Template learning (Workflow 2)** | **Agentic loop** | Bounded propose → test → refine search with acceptance criteria |
| Proposing a candidate for a layout heuristics cannot read | Agentic (LLM, future) | `LLMProposer` slot; disabled until a layout needs it |

The agent's loop structure is in place; its current proposer is deterministic, because all three layouts seen so far (two synthetic, one real) were learned without an LLM. An LLM is added only when a layout defeats the heuristics, and then sees only a redacted layout summary, never statement content.

## Components (`backend/src/bank_parser/`)

| Module | Responsibility |
| --- | --- |
| `pdf/` | Backend-neutral words + rectangles; `pypdfium2` (default; Apache/BSD; ~18× faster than pdfplumber, identical output — D-015), `pdfplumber` (MIT; ruled-table geometry for learning) or `pymupdf` (AGPL, optional); classification |
| `core/schema.py` | `Statement`, `StatementHeader`, `Transaction`, `StatementSummary`, `Provenance`; `Decimal` money; JSON v2 (v1 still loads) |
| `templates/` | Template model (pure data) and append-only versioned registry |
| `extraction/detection.py` | Marker matching |
| `extraction/table.py` | `word_columns` engine: rows from positioned words, multi-line descriptions, wrapped dates, rows split across pages, footer detection |
| `extraction/header.py` | Header rules: `regex` and positional `region` rules |
| `extraction/inference.py`, `unknown.py` | Unknown-layout inference and Workflow 1 |
| `extraction/generic_rules.py` | Layout-independent header rules; bank identity from the account's IFSC |
| `core/validation.py` | Balance chain, reconciliation, rows, header → `PASS / WARN / FAIL / SKIP` report |
| `learning/` | Template agent, proposers, privacy-safe markers |
| `storage/` | Content-addressed file store, SQLite |
| `pipeline.py` | Orchestration; separate extraction and learning pools |
| `api/` | FastAPI routes; the single-page UI lives in top-level `frontend/` |

## Templates

A template is the extraction contract for one bank **layout** at one **version**: identity (`bank_code`, `layout_id`, `version`), markers, column boundaries and roles, date formats, amount representation, header rules, required validation checks, provenance and regression status. It is JSON data; it contains no code and no personal data.

```text
backend/template_registry/<bank>/<layout>/v1.json   committed, reviewed
data/templates/<bank>/<layout>/v2.json      learned at runtime (gitignored) → promoted after review
```

- New versions only: files are created with exclusive-create, so no process can overwrite a version.
- A layout change is a new version (or a new `layout_id` when the labels change); older versions stay for older statements.
- Detection prefers the most specific match, then the newest version; a template whose output fails validation falls through to the next match, then to Workflow 1.

## Template agent bounds

| Limit | Default | Setting |
| --- | --- | --- |
| Candidate templates | 3 | `BANK_PARSER_LEARNING_MAX_CANDIDATES` |
| Refinements per candidate | 2 | `BANK_PARSER_LEARNING_MAX_REFINEMENTS` |
| Wall-clock time | 300 s | `BANK_PARSER_LEARNING_MAX_SECONDS` |
| LLM tokens | 0 (no LLM) | `LearningBudget.max_llm_tokens` |

Acceptance: no extraction problems; at least one transaction; every required check passes (the balance chain may be `WARN` only for an unprintable first row when no opening balance exists); at least 2 markers; markers match the source document and **no** document of another layout in the regression corpus. Otherwise: `HUMAN_REVIEW` with reasons.

## Large documents: no size cap, bounded resources

- No application size limit. `BANK_PARSER_MAX_UPLOAD_BYTES` is an optional infrastructure guard, unset by default.
- Uploads stream to disk in 1 MiB chunks while hashing; memory is one chunk.
- Pages are parsed one at a time and released (`page.close()` in every backend; pdfplumber's behaviour verified via Context7). Only header pages are retained.
- Work runs in bounded pools (`BANK_PARSER_EXTRACTION_WORKERS`, `BANK_PARSER_LEARNING_WORKERS`); learning runs in a separate process by default.

## Measured latency

Known-template path (classify + detect + extract + validate) on this machine, measured 2026-10-05 while it was under load. pdfplumber took about 3.5 s for the real statement when idle; the ratios are what to compare:

| Document | **pypdfium2 (default)** | pdfplumber | PyMuPDF (optional) |
| --- | --- | --- | --- |
| Synthetic SBI, 2 pages | **67 ms** | 530 ms | 37 ms |
| Synthetic HDFC, 1 page | **84 ms** | 660 ms | 30 ms |
| Real SBI, 9 pages | **0.41 s** | 8.0 s | 0.16 s |

Reproduce with `cd backend && uv run python scripts/benchmark.py`. Validation is under 1 ms in every case.

## Security and privacy

- Real statements, passwords, the database, learned templates and every extracted output live under gitignored paths.
- Passwords are held in memory for the duration of a job only; never stored or logged.
- Statement text is untrusted: the UI inserts it with `textContent`; no statement text ever reaches an LLM prompt.
- Template markers come only from a fixed label vocabulary, so templates cannot carry personal data.
- The app has no authentication and binds to `127.0.0.1`; do not expose it on a network yet.

## Not done yet

- OCR route for scanned pages (pages are flagged `NEEDS_OCR`).
- Layouts without ruled tables *and* without header text: inference needs either cell rectangles or header words.
- LLM proposer, MCP interface for agents (Phase 6), authentication, a job queue that survives restarts (jobs run in-process today).
- Classification and extraction each parse every page once; merging them would roughly halve known-path latency.
