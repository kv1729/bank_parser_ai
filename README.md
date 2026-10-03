# Bank Statement Parser

Turns text-based bank statement PDFs into validated, structured transaction data.

The approach is deterministic extraction per bank layout, checked by accounting invariants: every row must satisfy *previous balance − debit + credit = balance*, and totals must match the bank's own summary. LLMs, OCR and MCP are deferred until a benchmark shows they help.

## Status

Phase 0 (output contract and validation) is mostly done; Phase 1 (deterministic extraction) has early evidence. See `Bank_Statement_Parser_PLAN.md` §25.

- Typed `Statement` / `Transaction` schema with `Decimal` money and a strict JSON loader.
- `validate_balance_chain` reporting `PASS` / `WARN` / `FAIL` / `SKIP` with reason codes, rows and pages.
- Experiment on a real 9-page SBI statement: 81 transactions, balance chain and the bank's summary totals all reconcile; ~1.4 s per statement.
- Not yet: an end-to-end parser/CLI, layout templates, content-based document IDs.

## Repository layout

| Path | Contents |
| --- | --- |
| `bank_parser/` | Schema and validation (new code) |
| `tests/` | pytest suite; expected-output fixtures are local-only |
| `experiments/` | Small experiments backing entries in `DECISIONS.md` |
| `sample_data/` | Synthetic sample PDFs (real statements are gitignored) |
| `Bank_Statement_Parser_PLAN.md` | Plan and current status |
| `DECISIONS.md` | Decision log with evidence |
| `docs/ARCHITECTURE_REVIEW.md` | Initial architecture review |
| `*.py` at the root | Original prototype, kept for reference |

## Setup

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
venv\Scripts\python -m pytest
```

On a fresh clone most tests are skipped, because expected-output fixtures contain statement data and are not committed. See `tests/fixtures/README.md`.

## Data handling

Real statements, PDF passwords and parsed outputs are never committed. Processing is local; no statement data is sent to external services.
