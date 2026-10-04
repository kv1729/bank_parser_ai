# Bank Parser AI — Architecture Review

*Written 2026-10-02 · updated 2026-10-03 — see [Since this review](#since-this-review). The current architecture is [ARCHITECTURE.md](ARCHITECTURE.md).*

The current repo is a ~220-line prototype that extracts four header fields and no transactions, so it should be treated as a fresh start. Phase 1 should be deterministic per-bank templates checked by balance arithmetic, with LLMs and MCP deferred until a benchmark shows they help.

> **Scope note:** PLAN.md holds only review instructions, not a target architecture. This review therefore judges the ideas it implies — MCP, DocuClipper and hybrid LLM extraction — rather than a written spec.

## Contents

- [A. Current architecture](#a-current-architecture)
- [B. Gap analysis](#b-gap-analysis)
- [C. Recommended architecture (Phase 1)](#c-recommended-architecture-phase-1)
- [D. Risks and assumptions to validate](#d-risks-and-assumptions-to-validate)
- [E. Implementation phases](#e-implementation-phases)
- [F. Smallest first task](#f-smallest-first-task)
- [Since this review](#since-this-review)

---

## A. Current architecture

The code is five small modules with no entry point. The README says `python app.py`, but `app.py` does not exist, and nothing connects `parser.py` to `extractor.py`.

| Module | What it does | Notes |
| --- | --- | --- |
| `parser.py` | Extracts text with both pdfminer and pdfplumber, keeps whichever output is longer | Word positions are discarded; `segment_regions` (fixed line ranges 0–20 / 20–40 / 40+) is never called |
| `heuristics.py` | Regex for bank name, account number, IFSC | Patterns tied to one label spelling each |
| `extractor.py` | Regex for 3 fields, LLM for account holder name | Output is a plain dict of 4 header fields |
| `llm.py` | Ollama `/api/generate`, `qwen:4b`, temperature 0, 100 output tokens | Sends the whole statement to fetch one field; no schema, no retry |
| `utils.py` | JSON save/load, metrics log, token estimate | Metrics are never called |
| `model_testing.py` | Latency check across 4 Ollama models | Runs on import |

There is **no transaction extraction**, no typed schema, no amount handling and no tests.

### What the samples showed

The existing regex functions were run against the two PDFs in `sample_data/`.

| Check | HDFC sample | SBI sample |
| --- | --- | --- |
| Bank name / account number | Correct / correct | Correct / correct |
| IFSC | Correct | **Missed**: the PDF says `IFS Code :`, the regex expects `IFSC :` |
| pdfplumber table extraction | Header row only (table has no ruled lines) | Clean 7-column rows, multi-line descriptions intact |

### Bugs found

- **Fixed cache path (serious).** `cache/extracted.txt` does not depend on the input file, so a second statement silently gets the first statement's text. With financial data this is a cross-customer leak.
- **Crash on blank pages.** `page.extract_text() + "\n"` raises `TypeError` when a page has no text layer.
- **Windows encoding.** Files open without an explicit encoding, so non-Latin names or ₹ break under cp1252.
- **Loose bank-name regex.** It ignores case and has no word boundaries, so `SBI` matches inside `SBIN…` and `SBI CARDS`.
- **Model benchmark settings ignored.** `model_testing.py` sets `num_predict` and `temperature` at the top level, but Ollama reads them only under `options`, so its latency numbers used default settings.
- **Silent truncation.** `qwen:4b` is the old Qwen1.5 4B model with a small default context window, so multi-page statements will be cut off without an error.

**Worth reusing:** the account-number and IFSC regex ideas (once fixed), the shape of the Ollama wrapper, and pdfplumber itself, provided its word positions and tables are used instead of flattened text.

---

## B. Gap analysis

The biggest gaps are transaction extraction and arithmetic validation; almost every production capability is missing.

| Capability | Status |
| --- | --- |
| Transaction extraction | Missing (the core product) |
| Checks that the numbers add up (balance chain) | Missing (the most important gap) |
| Typed schema with Decimal amounts and Indian number format (`1,20,749.89`) | Missing |
| Position-based (column-aware) extraction | Missing |
| Detecting which bank's layout a statement uses | Missing (bank-name regex only) |
| Idempotency: cache and IDs based on file content | Wrong: cache uses a fixed path |
| Scanned and password-protected PDF handling | Missing |
| Tests and labelled correct-answer files | Missing |
| CLI or API entry point | Missing |
| Benchmark harness | Missing (`model_testing.py` only times a dummy prompt) |
| Metrics logging | Written but never called |

---

## C. Recommended architecture (Phase 1)

Phase 1 should be a deterministic pipeline with no LLM, where balance arithmetic, not model confidence, decides whether a result is correct. Every statement row must satisfy *previous balance − debit + credit = balance*, so statements check themselves.

```
PDF ─► 1. Ingest ─► 2. Layout ─► 3. Detect bank ─► 4. Extract ─► 5. Validate ─► 6. Output
                                       │                              │
                                       └─ unknown ─► (Phase 3: LLM)   └─ fail ─► human review
```

1. **Ingest.** The file's sha256 becomes the document ID and cache key. Reject encrypted files; flag pages with no text layer as needing OCR instead of skipping them.
2. **Layout.** Use pdfplumber words with x/y positions plus table detection; keep the positions.
3. **Detect the bank layout.** Match header keywords and the transaction table's header row to a template ID, or mark it unknown.
4. **Extract.** Each bank template defines labelled header fields and column boundaries taken from the table's header row. Merge multi-line descriptions and parse dates and amounts into Decimal.
5. **Validate.** Check the balance chain per row, opening and closing reconciliation, exactly one of debit or credit, date order, IFSC format and page continuity.
6. **Output.** A typed `Statement` with header, transactions and a validation report.

**Where the LLM fits later.** Its job is to infer the column layout of an unknown bank once per layout, then cache it, not to transcribe amounts row by row. Any rows it does extract must pass the same checks or go to human review.

**Where MCP adds value.** As a thin outer wrapper, for example a `parse_statement` tool that Claude Desktop or another agent can call once the parser works.

**Where MCP only adds latency and complexity.** Inside the pipeline. The stages are function calls in one process; putting MCP between them adds serialization and process hops, and invites an agent to decide steps that should be fixed code. Never stream PDF bytes or full transaction lists through an LLM's context; return summaries or IDs.

---

## D. Risks and assumptions to validate

The riskiest assumption is that LLM extraction is the route to accuracy; benchmark it before building on it.

### Assumptions to challenge

- **LLMs give the best accuracy.** Small local models (4B) are poor at transcribing long numeric tables and will invent or reorder amounts. For text-based PDFs, deterministic extraction plus balance checks will likely win on accuracy and cost.
- **The samples are representative.** Both PDFs were produced by WeasyPrint, so they look regenerated or synthetic, not genuine bank output. Real statements bring scans, passwords, watermarks, rotated pages and multi-account statements.
- **DocuClipper can be benchmarked freely.** It is a third-party SaaS; uploading real customer statements needs consent under India's DPDP Act. Use synthetic or consented documents only, and check that its terms allow benchmarking.
- **A hybrid approach works for every field.** Routing between methods is fine, but every output must record which method produced it, or failures cannot be debugged.

### Security, financial and data risks

- **PII in git.** `sample_data/` contains names, addresses, account numbers, an email address and a PAN. If any of it is real, it is already in history; removing it means rewriting history, which needs an explicit decision.
- **Prompt injection.** Transaction descriptions (UPI remarks, NEFT beneficiary names) can be written by anyone who sends money. Treat statement text as untrusted data and never let LLM output skip validation.
- **Data leaking via prompts and logs.** Full statements currently go into prompts and `print` output. Keep the model local or send the minimum, and redact logs.
- **Float amounts.** Use Decimal everywhere.
- **Duplicate transactions.** Re-uploads and statements with overlapping date ranges will duplicate rows downstream. Each transaction needs a stable ID from account, date, amount, running balance and row position.
- **Hostile or broken PDFs.** A malformed file can exhaust memory or hang the parser. Set page, size and time limits.

---

## E. Implementation phases

Build correctness and measurement first, then add LLM, OCR and MCP only where the benchmark shows a gap.

1. **Phase 0 — Groundwork.** Typed schema with Decimal amounts, hand-labelled correct output for both samples, pytest, a cache keyed by file hash, and a decision on the sample PII.
2. **Phase 1 — Deterministic MVP.** The pipeline in section C for SBI and HDFC. SBI can use pdfplumber's table extraction directly; HDFC needs column boundaries from word positions. Validators and a CLI.
3. **Phase 2 — Benchmark harness.** At least 20 statements across about 5 banks, each with labelled correct output.
4. **Phase 3 — LLM fallback for unknown layouts.** JSON-schema-constrained output, checked by the same validators, with failures routed to human review.
5. **Phase 4 — Scanned PDFs and service.** An OCR route, an API, and persistence with deduplication.
6. **Phase 5 — MCP wrapper,** only if an agent actually needs to call the parser.

### How to benchmark

Run all four approaches on the same labelled set: deterministic templates, LLM-only, hybrid, and DocuClipper (its CSV export converted into the same schema).

| Metric | What it measures |
| --- | --- |
| Statements fully correct (%) | The business metric: every header field and every row right |
| Row precision and recall | Rows matched on date, amount and debit/credit |
| Field accuracy | Per-field correctness on matched rows |
| Header exact match | Bank, account number, IFSC, holder name |
| Balance-check pass rate | How often output passes arithmetic validation |
| Latency p50 / p95 (s) | Speed per statement |
| Cost per statement | API or compute cost |
| Run-to-run consistency | Differences across 5 repeated runs |

---

## F. Smallest first task

Define the `Statement` and `Transaction` schema with Decimal amounts, and hand-write the correct output for the SBI sample (about 30 rows) as JSON. Add one function, `validate_balance_chain(statement)`, with a pytest that passes on that file and fails when any single amount is changed.

It needs no PDF parsing and no LLM. It fixes the output contract, creates the first benchmark answer file, and builds the check every later method (template, LLM, DocuClipper) will be judged by.

---

## Since this review

The review was followed by `Bank_Statement_Parser_PLAN.md`, which adopted its recommendations. Evidence for each point is in `DECISIONS.md`.

| Review item | What happened |
| --- | --- |
| F. Smallest first task | Done: `bank_parser/` schema with `Decimal` money, `validate_balance_chain`, 69 pytest cases (D-001, D-003) |
| "SBI can use pdfplumber table extraction directly" (E, Phase 1) | **Only partly true.** On the synthetic SBI sample, pdfplumber silently drops a row split across pages; the balance chain catches it (D-002). On a real SBI statement it was correct on all 81 rows (D-005) |
| Bank layout detection by header keywords (C, step 3) | **Revised.** The real SBI statement draws its column headings as vector shapes, not text. Detection must use table geometry and labels such as "Statement Summary" |
| Validation as the source of truth (C) | **Confirmed** on real data: balance chain plus the bank's printed summary (Dr/Cr counts, totals, closing balance) all reconcile exactly |
| Password-protected PDFs (B) | pdfplumber opens them with a password; no extra library needed |
| Latency | Measured, not assumed: ~1.4 s for a 9-page statement, ~200 ms/page, almost all in table finding; validation 0.1 ms (D-005) |
| PII in sample data (D) | Still open (D-004). Expected-output files are now gitignored and never committed (D-006) |
| MCP / LLM / OCR | Not used. Nothing so far has needed them |

