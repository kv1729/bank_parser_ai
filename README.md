# Bank Statement Parser

Upload a text-based bank statement PDF; get validated structured data back: account and statement header, every transaction, and a validation report.

- **Known layouts** take a deterministic, versioned template.
- **Unknown layouts** are extracted immediately by inferring the table layout. In parallel, a bounded template-learning agent turns the layout into a new template, so the next statement of that layout takes the known path. Extraction never waits for learning.
- **Validation is arithmetic:** every row must satisfy *previous balance − debit + credit = balance*, and totals must match the summary the bank prints. Money is `Decimal` throughout.

**Start here:** [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) (how it works) · [`STAGES.md`](STAGES.md) (progress) · [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) + [`docs/diagrams/`](docs/diagrams/) (design) · [`docs/DECISIONS.md`](docs/DECISIONS.md) (why).

## Repository layout

```text
backend/                 Python service (uv project)
  src/bank_parser/         the application package
    core/                    output schema + validation (domain, no I/O)
    pdf/  extraction/  templates/  learning/  storage/  pipeline.py
    api/                     FastAPI routes
  template_registry/       reviewed, versioned templates (this system's "models")
  tests/                   pytest; fixtures/pdfs = synthetic samples
  scripts/                 benchmark, sensitive-string refresh
  experiments/             one-off research, not shipped
frontend/                web UI (single static page served by the API)
docs/                    guide, architecture, plan, decisions, diagrams, history/
scripts/                 repository tooling (privacy gate)
legacy/prototype/        archived original prototype
.githooks/ .github/      privacy hooks · CI
data/                    runtime only, gitignored: uploads, database, learned templates
sample_data/             local private statements, gitignored
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```
git config core.hooksPath .githooks          # once per clone: privacy gate
cd backend
uv sync                                      # add --all-extras for PyMuPDF (AGPL; see docs/DECISIONS.md D-007)
uv run pytest
uv run uvicorn bank_parser.api.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000, upload a PDF (with its password if it has one), and watch it process.

## What is extracted

| Header | Each transaction |
| --- | --- |
| bank name and address, account holder and address, account number, IFSC, branch, currency, statement period, opening/closing balance, printed totals | date, value date, description, reference, debit/credit, amount + direction, running balance, page, any extra columns |

Documents end as `EXTRACTED` (arithmetic verified), `NEEDS_REVIEW`, `NEEDS_PASSWORD`, `NEEDS_OCR` (no text layer; OCR is a later stage) or `FAILED`.

## Configuration

Environment variables, all optional:

| Variable | Purpose |
| --- | --- |
| `BANK_PARSER_DATA_DIR` | Runtime data folder (default `data/` at the repository root) |
| `BANK_PARSER_TEMPLATES_DIR` | Reviewed templates (default `backend/template_registry/`) |
| `BANK_PARSER_FRONTEND_DIR` | Web UI folder (default `frontend/`) |
| `BANK_PARSER_PDF_BACKEND` | `pdfplumber` or `pymupdf` |
| `BANK_PARSER_EXTRACTION_WORKERS`, `BANK_PARSER_LEARNING_WORKERS` | Worker pool sizes |
| `BANK_PARSER_LEARNING_MAX_SECONDS`, `BANK_PARSER_LEARNING_MAX_CANDIDATES`, `BANK_PARSER_LEARNING_MAX_REFINEMENTS` | Template-agent limits |
| `BANK_PARSER_MAX_UPLOAD_BYTES` | Optional upload limit (unset = no limit) |

## Data handling

Processing is local; nothing is sent to external services. Uploaded statements, the database, learned templates and every extracted output live in `data/`. Private statements live in `sample_data/`. Both folders are gitignored, and the pre-commit hook blocks them. PDF passwords are used in memory only and never stored. The app has no authentication: keep it on `127.0.0.1`.
