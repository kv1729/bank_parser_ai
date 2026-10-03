# Bank Statement Parser

Upload a text-based bank statement PDF; get validated structured data back: account and statement header, every transaction, and a validation report.

- **Known layouts** take a deterministic, versioned template.
- **Unknown layouts** are extracted immediately by inferring the table layout. In parallel, a bounded template-learning agent turns the layout into a new template, so the next statement of that layout takes the known path. Extraction never waits for learning.
- **Validation is arithmetic:** every row must satisfy *previous balance − debit + credit = balance*, and totals must match the summary the bank prints. Money is `Decimal` throughout.

Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Decisions and evidence: [`DECISIONS.md`](DECISIONS.md). Plan: [`Bank_Statement_Parser_PLAN.md`](Bank_Statement_Parser_PLAN.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync                        # add --extra fast for PyMuPDF (AGPL; see DECISIONS.md D-007)
uv run pytest
uv run uvicorn bank_parser.app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000, upload a PDF (with its password if it has one), and watch it process.

## What is extracted

| Header | Each transaction |
| --- | --- |
| bank name and address, account holder and address, account number, IFSC, branch, currency, statement period, opening/closing balance, printed totals | date, value date, description, reference, debit/credit, amount + direction, running balance, page, any extra columns |

Documents end as `EXTRACTED` (arithmetic verified), `NEEDS_REVIEW`, `NEEDS_PASSWORD`, `NEEDS_OCR` (no text layer; OCR is a later phase) or `FAILED`.

## Configuration

Environment variables, all optional: `BANK_PARSER_DATA_DIR` (default `data/`), `BANK_PARSER_PDF_BACKEND` (`pdfplumber` | `pymupdf`), `BANK_PARSER_EXTRACTION_WORKERS`, `BANK_PARSER_LEARNING_WORKERS`, `BANK_PARSER_LEARNING_MAX_SECONDS`, `BANK_PARSER_LEARNING_MAX_CANDIDATES`, `BANK_PARSER_LEARNING_MAX_REFINEMENTS`, `BANK_PARSER_MAX_UPLOAD_BYTES` (unset = no limit).

## Repository layout

| Path | Contents |
| --- | --- |
| `bank_parser/` | The application: PDF backends, schema, templates, extraction, validation, template agent, storage, pipeline, web app |
| `template_registry/` | Reviewed, committed templates and the regression-corpus manifest |
| `tests/` | pytest suite; local-only fixtures are skipped when absent |
| `experiments/` | `benchmark.py` (latency per stage) and earlier experiments |
| `sample_data/` | Synthetic sample PDFs; real statements are gitignored |
| `docs/` | Architecture and the initial review |
| `*.py` at the root | Original prototype, kept for reference |

## Data handling

Processing is local; nothing is sent to external services. Uploaded statements, the database, learned templates and every extracted output live in `data/`, which is gitignored. PDF passwords are used in memory only and never stored. The app has no authentication: keep it on `127.0.0.1`.
