# Runbook — set up, start, use and stop the app

*Step-by-step reference for running the bank statement parser on your machine (Windows). Updated 2026-10-07.*

## 0. What actually runs

There is **one program to start**: the backend server. It also serves both web pages, so there is no separate frontend or database server to start.

| Part | Where it lives | Do you start it? |
| --- | --- | --- |
| Backend (FastAPI + processing) | `backend/` | **Yes** — with `start-app.cmd` (section 3) |
| Web pages (upload, review) | `frontend/` | No — served by the backend |
| Database (SQLite) | `data/bank_parser.db` | No — a file, created automatically |
| Template learning | inside the backend | No — runs automatically in the background |

Two pages, both on http://127.0.0.1:8000:
- **http://127.0.0.1:8000/** — upload a statement and watch it process.
- **http://127.0.0.1:8000/review** — compare a PDF with its extracted output, flag mistakes, add remarks.

## 1. One-time setup (per computer)

Do these once. Each has a check so you know it worked.

| # | Step | Command (PowerShell) | Check |
| --- | --- | --- | --- |
| 1 | Install **uv** (manages Python and libraries) | `winget install --id=astral-sh.uv -e`<br>or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | Open a **new** terminal: `uv --version` prints a version |
| 2 | Get the code | `git clone https://github.com/kv1729/bank_parser_ai.git` (or use your existing folder) | Folder has `backend/`, `frontend/`, `start-app.cmd` |
| 3 | Turn on the privacy gate | `cd E:\Ted\Projects\bank_parser_ai` then `git config core.hooksPath .githooks` | `git config --get core.hooksPath` prints `.githooks` |
| 4 | Install the app's libraries | `cd backend` then `uv sync` | Ends without errors; `backend\.venv` exists |
| 5 | Put private statements in place | Copy PDFs into `sample_data\`; if they share a password, put it alone in `sample_data\.pdf_password` | Both are gitignored: `git status` does not list them |
| 6 | Build the privacy list from your statements | from `backend\`: `uv run python scripts/refresh_sensitive_strings.py` | Prints `scanned N local statement(s)` |

Repeat step 6 whenever you add a new private statement.

## 2. Before each session: get the latest code

```powershell
cd E:\Ted\Projects\bank_parser_ai
git switch main
git pull
```

**Ensure:**
- `git status` says *"up to date with 'origin/main'"*.
- You are on `main`, unless you are deliberately testing a stage branch.

A server that is already running keeps the **old** code until you restart it (section 5, then section 3).

## 3. Start the app

### Option A — the launcher (recommended)

Double-click **`start-app.cmd`** in the project folder, or run it from any terminal:

```powershell
E:\Ted\Projects\bank_parser_ai\start-app.cmd
```

It always starts from the right folder, ignores old Python environments, checks that `uv` is installed and the port is free, installs missing libraries, then starts the server.

**You should see:**
```
Starting on http://127.0.0.1:8000
  Upload page : http://127.0.0.1:8000/
  Review page : http://127.0.0.1:8000/review
...
INFO:     Application startup complete.
```

**Keep that window open** — closing it stops the app. To use another port: `start-app.cmd -Port 8001`.

### Option B — by hand

```powershell
cd E:\Ted\Projects\bank_parser_ai\backend      # must be backend\, not the project root
uv run uvicorn bank_parser.api.main:app --host 127.0.0.1 --port 8000
```

### Check it is running

Open http://127.0.0.1:8000/review in your browser. The list of PDFs appears on the left.

## 4. Use it

### Upload page (`/`)
1. Choose a PDF; type its password if it has one.
2. Click **Upload**; the status updates by itself until `EXTRACTED`, `NEEDS_REVIEW`, `NEEDS_PASSWORD` or `NEEDS_OCR`.

### Review page (`/review`)
1. **Left:** pick a PDF. Files from `sample_data\` are imported and processed on first click; password-protected ones use `sample_data\.pdf_password`, otherwise the page asks for the password (kept in memory only).
2. **Middle:** scroll the PDF.
3. **Right:** tick every output that is **wrong**; write observations in **Remarks**. It saves automatically ("Saved …" at the bottom).
4. Click a transaction to jump to its page. Amber-edged rows are ones the validator questioned.
5. After a code fix, click **Re-extract with current code** (top right, next to the extraction number) to get fresh output. Your previous review stays with the previous extraction.

## 4a. See the extracted output without the UI

**Where it is stored:** everything is in one SQLite database file, `E:\Ted\Projects\bank_parser_ai\data\bank_parser.db`. Inside it:

| Table | Holds |
| --- | --- |
| `documents` | One row per PDF: id (SHA-256 of the file), file name, status, page count |
| `extractions` | One row per extraction run. `statement_json` holds the full output as **JSON** (header, printed totals, every transaction); `report_json` the validation report |
| `reviews` | Your ticks and remarks, tied to one extraction |

Each **Re-extract** adds a new extraction; older ones are kept. Extraction numbers are shared by all documents, so the latest for one statement may be #8 even if it has only 6 extractions. The output is JSON *inside* the database, so a spreadsheet needs an export first:

**Spreadsheet (CSV, opens in Excel)** — from `backend\`:

```powershell
uv run python scripts/export_extraction.py --list            # documents, their ids and extraction numbers
uv run python scripts/export_extraction.py --doc a9941c06    # latest extraction of that document
uv run python scripts/export_extraction.py --doc a9941c06 --extraction 5   # an older one
uv run python scripts/export_extraction.py --doc a9941c06 --json           # also the raw JSON
```

It prints the paths of what it wrote, in `data\exports\`:
- `<doc>_extraction_<n>_transactions.csv` — one row per transaction (date, description, reference, debit, credit, amount, direction, balance, page);
- `<doc>_extraction_<n>_header.csv` — account and statement fields, printed totals, validation result;
- `<doc>_extraction_<n>.json` — with `--json`, the exact stored JSON.

Double-click the CSV to open it in Excel. The database is opened read-only, so this is safe while the app is running.

**Raw JSON in the browser** (app running): http://127.0.0.1:8000/api/documents lists documents with their `id`; http://127.0.0.1:8000/api/documents/&lt;id&gt; shows the latest extraction.

**Browse the database itself:** the free *DB Browser for SQLite* → *Open Database Read Only* → `data\bank_parser.db` → *Browse Data* → `extractions`.

`data\` (database and exports) is gitignored and contains your statement data — keep it on your machine.

## 5. Stop the app

Press **Ctrl+C** in the server window (or close the window). Check: http://127.0.0.1:8000 no longer loads.

## 6. Tests and timings (optional)

From `backend\`:

| Task | Command | Expect |
| --- | --- | --- |
| Run all tests | `uv run pytest` | `… passed` and no failures (some may be "skipped" on other machines) |
| Measure speed | `uv run python scripts/benchmark.py` | Timings per stage per PDF engine |

## 7. Troubleshooting

| What you see | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'bank_parser'`, a traceback, or "aborted" right after starting | Started from the project root, so `uv` used the **old `.venv` at the root** (left over from before the restructure) | Use `start-app.cmd`, or `cd backend` first (section 3B). The old root environments were renamed to `.venv.old\` and `venv.old\` (2026-10-07) and are unused — delete them whenever you like |
| `warning: VIRTUAL_ENV=… does not match the project environment path` | An old environment is activated in your terminal | Harmless. The launcher clears it; or open a fresh terminal |
| `Port 8000 is already in use` | The app (or something else) is already running | Open http://127.0.0.1:8000/review — it may already be up. Or start on another port: `start-app.cmd -Port 8001` |
| `running scripts is disabled on this system` | PowerShell blocks `.ps1` files | Use `start-app.cmd` (it bypasses this for this one script) |
| `'uv' is not recognized` | uv not installed, or terminal opened before installing | Section 1, step 1; then open a **new** terminal |
| `uv sync` fails | No internet, or a broken download | Check your connection, run `uv sync` again from `backend\` |
| Review page shows old results or old behaviour | The server is still running old code | Stop it (section 5), `git pull` (section 2), start again (section 3), then **Re-extract** the PDF |
| Some PDF pages show "could not be shown" | A page failed to render | Click the message to retry. If many fail, check the server window for errors |
| Status `NEEDS_PASSWORD` | Encrypted PDF without the right password | On `/review`, type the password; or put it in `sample_data\.pdf_password` and select the file again |
| Status `NEEDS_OCR` | Scanned PDF (no text layer) | Not supported yet (Stage 10) |
| The server window closes or stops on its own | Windows was low on memory, or the window was closed | Start it again; close other heavy programs |

## 8. Checklist — what to ensure every time

- [ ] Latest code: `git switch main` + `git pull` (section 2).
- [ ] Start with **`start-app.cmd`** (or from `backend\`, never the project root).
- [ ] The server window shows `Application startup complete` and stays open.
- [ ] http://127.0.0.1:8000/review loads.
- [ ] After any code update: restart the server, then **Re-extract** the PDFs you are reviewing.
- [ ] Private statements and passwords stay in `sample_data\` (never commit them — the hook blocks it anyway).
- [ ] Stop with Ctrl+C when finished.
