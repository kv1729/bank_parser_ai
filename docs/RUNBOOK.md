# Runbook — set up, start, use and stop the app

*Step-by-step reference for running the bank statement parser on your machine (Windows). Updated 2026-10-15.*

## 0. What actually runs

There is **one program to start**: the backend server. It also serves both web pages, so there is no separate frontend or database server to start.

| Part | Where it lives | Do you start it? |
| --- | --- | --- |
| Backend (FastAPI + processing) | `backend/` | **Yes** — with `start-app.cmd` (section 3) |
| Web pages (upload, review) | `frontend/` | No — served by the backend |
| Database (SQLite) | `data/bank_parser.db` | No — a file, created automatically |
| Template learning | inside the backend | No — runs automatically in the background |
| Local AI model (Ollama) | installed program, model `qwen3.5:9b` | No — Ollama starts with Windows; the app uses it only for layouts its rules cannot learn (section 5b) |

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
| 5 | Put private statements in place | Copy PDFs into `sample_data\` (not `data\documents\` — that is the app's internal store). Put their passwords in `sample_data\.pdf_password`, **one password per line**; each PDF is opened with whichever line works | Both are gitignored: `git status` does not list them |
| 6 | Build the privacy list from your statements | from `backend\`: `uv run python scripts/refresh_sensitive_strings.py` | Prints `scanned N local statement(s)` |
| 7 | (Optional) the local AI model | Install Ollama from ollama.com, then `ollama pull qwen3.5:9b` (6.6 GB download) | `ollama list` shows `qwen3.5:9b` |

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
3. **`LEARNING`** means the layout is new and the first attempt could not be verified. The template agent is learning it in the background, using the local AI model if the rules cannot, which can take a minute. Leave the page open: the checked result replaces the status by itself (`EXTRACTED`, or `NEEDS_REVIEW` with the agent's reason if it could not learn the layout). While a statement is `LEARNING`, uploading it again or **Re-extract** does nothing (one learning job per statement). If the app is stopped meanwhile, the statement shows `NEEDS_REVIEW` ("interrupted") after the restart: reprocess it.

### Review page (`/review`)
1. **Left:** pick a PDF. Files from `sample_data\` are imported and processed on first click; password-protected ones use `sample_data\.pdf_password`, otherwise the page asks for the password (kept in memory only).
2. **Middle:** scroll the PDF.
3. **Right:** for each account field choose **OK**, **Wrong**, or **Missing** (printed on the statement but not extracted, or only partly extracted). Tick a transaction row if any of its values is wrong; describe rows the extractor missed in **Remarks**. It saves automatically; the bar at the bottom of the panel always shows **Saved ✓** with the time in IST, or **Save failed: …** (then press **Save review** again).
4. Click a transaction to jump to its page. Amber-edged rows are ones the validator questioned.
5. **US statements** (Stage 10.6): single-table US statements are read like Indian ones; rows without a year get it from the statement period, and the routing number is checked (`invalid_routing_number` means a digit is wrong). Statements split into separate Deposits/Withdrawals/Checks sections are not supported yet (Stage 16).
6. **Text warnings** (validation list, Stage 10.5): `text_not_captured` = some text inside the table belongs to no row; `empty_description`; `description_start_at_end` = a description ends with words that usually start one, so a line has probably moved from the next row; `label_in_value` = an account value contains a label word. They are signs to look at the PDF there, not proof of an error.
7. **Agent's self-review** (box above the account fields): what the system checked, what it is unsure of, and a confidence tag next to every field and column (hover for the reason). For each "unsure" item click **Agree** (it deserved a look) or **Disagree** (the worry was wrong or not useful), with an optional note. It saves with your review. Once you have marked fields, a **Calibration** line says whether anything you marked wrong had been rated high.
8. **Autonomy** (Stage 11): open **/autonomy** (link at the top of the upload and review pages) to see each layout's run of reviews without a correction against the threshold (`BANK_PARSER_AUTONOMY_THRESHOLD`, default 10), what is waiting for your review and why, your corrections per week, and layout changes. Each statement shows **Your review: needed / not needed** with the reason. The agent approves golden statements only when `BANK_PARSER_AUTONOMY=on` is set before starting the app, and only for layouts that have earned it; such goldens say "approved by the agent", and you can still mark them.
9. **Memory for the local model** (Stage 10.8): the model needs about 6 GB of free memory. With less, it fails to load and the statement waits for human review (it is never read wrongly); the job's reason then says `llm: model server ... failed`. Close other large programs and press **Re-extract** to try again. Its first answer after a start can take a few minutes while it loads.
10. **What the agent learns from you** (Stage 10.7): approving a golden statement, marking fields or rows, and Agree/Disagree are stored as examples (never values). The self-review box shows how many. A field you corrected on a layout is not rated high again until you approve that layout again; a worry you called not useful twice is greyed and listed last. For approvals and reviews made before this existed, run `uv run python scripts/build_examples.py` once (from `backend/`; safe to repeat).
11. After a code fix, click **Re-extract with current code** (top right, next to the extraction number) to get fresh output. Your previous review stays with the previous extraction; the page shows a note about it with **Copy that review here**.

## 4a. See the extracted output without the UI

**Where it is stored:** everything is in one SQLite database file, `E:\Ted\Projects\bank_parser_ai\data\bank_parser.db`. Inside it:

| Table | Holds |
| --- | --- |
| `documents` | One row per PDF: id (SHA-256 of the file), file name, status, page count |
| `extractions` | One row per extraction run. `statement_json` holds the full output as **JSON** (header, printed totals, every transaction); `report_json` the validation report |
| `reviews` | Your marks (wrong, missing) and remarks, tied to one extraction |
| `golden_snapshots` | Approved (golden) outputs, one version per approval; older versions kept |

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

## 5a. Golden statements and the regression check (Stage 9)

A **golden** statement is one you checked completely and approved. From then on, every code or template change must reproduce its output exactly.

**Approve one** (in `/review`):
1. Re-extract it with the current code, check every field against the PDF, and set everything to **OK** (no Wrong, no Missing). Save.
2. Click **Approve as golden** (bottom bar). If validation has warnings (for example "no opening balance is printed"), the page lists them and asks you to confirm.
3. If the PDF is encrypted, its password must be a line in `sample_data\.pdf_password`, so the statement can be re-checked later. The password itself is never stored.

The output panel then shows **Golden v1 · approved … IST**. If a later extraction differs, the panel lists every differing field. If the new output is right, check it and click **Approve again** (v2; v1 is kept).

**Run the check yourself** (from `backend\`):

| Command | What it does |
| --- | --- |
| `uv run python scripts/regress.py` | Re-extracts every golden statement and compares field by field. `PASS` or `DIFF` with the field names; exit code 1 on any difference |
| `uv run python scripts/regress.py --show-values` | The same, with the actual values instead of masks (`Aaaa 99`) |
| `uv run python scripts/regress.py --backends both` | Also runs the second PDF engine (reported only) |

**After approving a statement with a new layout**, make an anonymised copy of it for CI (from `backend\`):

```powershell
uv run python scripts/anonymise_layout.py --doc <first 8 characters of the document id> --name <bank>-<layout id>
```

It prints only checks and counts, and refuses if any real word would survive. Look at the preview pictures it lists (`data\exports\layout-preview\<name>\page-N.png`) before committing `backend\tests\fixtures\layouts\<name>\`.

It also runs automatically before every `git push` (`.githooks/pre-push`). A failing check blocks the push. Don't bypass it with `--no-verify`; re-approve in `/review` if the change is intended. Reports are saved in `data\regress\`.

## 5b. The local AI model (Stage 10)

When a statement arrives whose layout no template knows and the rules cannot learn it, the template agent asks a **local** model to name the table's columns (date, description, debit, …). Code then builds the template and checks it exactly like any other: the arithmetic, the column contents, other layouts, and the golden statements. The model runs on this computer through Ollama; nothing is sent anywhere else, and the app refuses any model server that is not on this computer.

| What | Detail |
| --- | --- |
| Model | `qwen3.5:9b` (Apache-2.0), chosen by the benchmark in D-023 |
| Cost per new layout | about 20–30 s and 6.4 GB of memory while it runs; known layouts never use it |
| Turn it off | set `BANK_PARSER_LLM_MAX_TOKENS=0` before starting the app |
| Another model | `BANK_PARSER_LLM_MODEL=<name>` (it must be installed: `ollama pull <name>`) |
| Ollama not running / model missing | the agent skips the AI step and works as before |

**Re-run the model benchmark** (from `backend\`; prints scores only):

```powershell
uv run python experiments/llm_choice.py --model qwen3.5:9b
```

Follow its progress in another window: `Get-Content ..\data\exports\llm-choice\progress.log -Wait`. The report is saved in `data\exports\llm-choice\`.

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
| After **Re-extract**, a field printed on the statement is still empty, or the review page has no **OK / Wrong / Missing** choice | The server window was started **before** the code was updated: new templates are read from disk, but the program is still the old one (2026-10-14, D-021) | Stop the server (Ctrl+C), start it again with `start-app.cmd`, then **Re-extract** again |
| Some PDF pages show "could not be shown" | A page failed to render | Click the message to retry. If many fail, check the server window for errors |
| Status `NEEDS_PASSWORD` | Encrypted PDF without the right password | On `/review`, type the password; or add it as a new line in `sample_data\.pdf_password` and select the file again |
| A PDF copied into `data\documents\` does not appear anywhere | That folder is the app's internal store (files named by their SHA-256) | Put statements in `sample_data\` or upload them |
| Status `NEEDS_OCR` | Scanned PDF (no text layer) | Not supported yet (Stage 13) |
| The server window closes or stops on its own | Windows was low on memory, or the window was closed | Start it again; close other heavy programs |

## 8. Checklist — what to ensure every time

- [ ] Latest code: `git switch main` + `git pull` (section 2).
- [ ] Start with **`start-app.cmd`** (or from `backend\`, never the project root).
- [ ] The server window shows `Application startup complete` and stays open.
- [ ] http://127.0.0.1:8000/review loads.
- [ ] After any code update: restart the server, then **Re-extract** the PDFs you are reviewing.
- [ ] Private statements and passwords stay in `sample_data\` (never commit them — the hook blocks it anyway).
- [ ] Stop with Ctrl+C when finished.
