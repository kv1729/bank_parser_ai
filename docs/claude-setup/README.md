# Setting up a new laptop

The code, templates and docs are in git. Private data is not, by design: statements, the PDF password and `data/` (database, uploads, golden snapshots).

## 1. Claude Code settings

Copy these files out of the repository:

| From | To |
| --- | --- |
| `docs/claude-setup/global-CLAUDE.md` | `~/.claude/CLAUDE.md` (the owner's global rules) |
| `docs/claude-setup/memory/*.md` | `~/.claude/projects/<project path, with : \ / replaced by ->/memory/` (e.g. `E--Ted-Projects-bank-parser-ai`) |

Claude Code creates the project folder the first time it is opened in the repository; copy the memory files after that.

## 2. The app

From the repository root:

1. `git config core.hooksPath .githooks` (privacy gate and pre-push golden check).
2. `cd backend && uv sync`.
3. Put the statements back in `sample_data/`, and the passwords in `sample_data/.pdf_password` (one per line). Both stay out of git.
4. `cd backend && uv run python scripts/refresh_sensitive_strings.py` (the local list of identifiers the hooks block).
5. Install Ollama and run `ollama pull qwen3.5:9b` (needs about 6 GB free memory; D-028).
6. Start the app with `start-app.cmd` (see `docs/RUNBOOK.md`).

## 3. Rebuild the local data

`data/` starts empty. The learned templates for BOI, ICICI and the second SBI layout are committed in `backend/template_registry/`, so nothing has to be learned again.

1. Upload the statements again on `/`.
2. In `/review`, check each one and approve it as golden. Golden snapshots, the per-layout track record (`/autonomy`) and the training examples are rebuilt from these approvals.
3. Run `uv run python scripts/regress.py` from `backend/` to confirm the goldens.

The old laptop's corrections from 2026-10-14 (BOI and ICICI) are not carried over, so those layouts start their track record clean (compare open owner decision 2 of D-029).
