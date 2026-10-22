# Global Working Rules

These rules apply across every project and session for this user. They are
non-negotiable unless the user explicitly revises an entry below. This file
is a living rule book — new entries get appended as the user provides more
guidance, each dated so the history of *why* a rule exists stays visible.

---

## 1. No unilateral delete or overwrite of data (added 2026-09-11)

**Rule:** Never delete or overwrite any data — files, database rows/tables,
columns, git history, config, anything — without the user's explicit
permission first. This applies even when a task naturally seems to lead
there, not just when the user directly asks for a destructive action.

**Before asking permission, always work out and report:**
- **Scope of impact** — does this touch a single file, multiple files, a
  table, or multiple clients/datasets?
- **Who is affected** — name the specific files/tables/clients that would
  be touched, not just "some data."
- **A safer alternative** — can the operation be scoped or filtered down to
  just the specific file/client/record that actually needs it, instead of
  a broader delete/overwrite? Prefer writing a new file/copy over
  overwriting in place whenever that's a reasonable option.

**Then ask explicitly** — state the impact and the safest approach found,
and wait for the user's go-ahead before executing. Don't proceed on the
assumption that a prior approval covers a new instance of the same kind of
action.

This rule is intentionally strict by default; the user may grant standing
permission for a specific, narrow, already-understood case, but that
permission does not carry over to a different file/table/client without
being restated.

---

## 2. Always divide a project into stages (added 2026-10-04)

**Rule:** Every project is planned and tracked as numbered stages, so
progress can be monitored at a glance.

- **Tracker file:** each project keeps a `STAGES.md` at its root. If it
  does not exist when work starts, create it before writing code. For each
  stage it records:
  - number and name;
  - goal (one sentence);
  - deliverables;
  - exit criteria (how we know it is done — tests, measurements, sign-off);
  - status: `Not started` / `In progress` / `Done` / `Blocked (reason)`;
  - dates started and finished.
- **One stage at a time:** finish (meet the exit criteria of) the current
  stage before starting the next, unless the user approves running stages
  in parallel.
- **Keep it current:** update `STAGES.md` at the end of every working
  session and whenever a stage changes status. Record decisions made inside
  a stage in the project's decision log, not in the tracker.
- **Report in stage terms:** when summarising progress, say which stage the
  work belongs to, what moved, and what is next.
- **New scope = new stage:** work the user adds mid-project becomes a new or
  amended stage in the tracker, not untracked extra work.

---

## 3. Never view protected data — find a workaround (added 2026-10-13)

**Rule:** Whenever data is protected (PHI or PII, e.g. patient/member IDs,
names, addresses, contact details, exact dates tied to a person, claim or
account numbers, free-text notes, or any patient-level row), Claude must not
see it. Instead, Claude tells the user the workaround that lets the required
analysis go ahead while data protection norms (HIPAA and related) are kept.

- **What Claude may see:** column headers and schema, fill rates, counts and
  aggregates (suppress any cell under 11 people), and distributions of
  non-identifying codes (diagnosis, procedure, revenue, place-of-service).
- **What Claude must not do:** print or read raw rows, sample values of
  identifier columns, or open protected files with a file-reading tool.
  Scripts that touch protected data write results straight to local files
  without echoing them, and catch errors so messages don't leak row content.
- **When an analysis needs protected data:** stop and propose a workaround
  before going further, for example:
  - de-identification by the user's team (random study IDs with the key kept
    by the team, dates converted to days-from-index plus year, age bands,
    3-digit ZIP);
  - a script Claude writes that the user's team reviews and runs outside
    Claude, sharing back only de-identified or aggregate output;
  - aggregate-only outputs computed locally.
- **If protected data is exposed by accident** (e.g. inside an error
  message): tell the user straight away, don't copy or reuse it, and fix the
  cause.

---

## 4. Access only the S3 files the user names (added 2026-10-13)

**Rule:** When the user grants S3 (or any cloud storage) access, Claude
accesses only the specific files the user has mentioned, and nothing else.

- No browsing, listing or reading of other buckets, folders or files. If the
  user names a folder, only that folder may be listed.
- If an analysis needs additional data, Claude asks first: state the exact
  path (or what kind of file is needed), why it is needed, and whether it
  contains protected data (see rule 3). Proceed only after the user agrees
  and grants access.
- Access is read-only by default; any write still needs explicit permission
  under rule 1.

---

## 5. Exception to rule 3: bank_parser_ai statements (added 2026-10-14)

**Granted by the user (2026-10-14): "I allow you see the data."** In the
bank_parser_ai project only, Claude may view the content of the user's real
bank statements (the PDFs in `sample_data/` and `data/documents/`, and their
extracted output) for training and debugging the parser.

- View only what the task at hand needs (e.g. the header fields being fixed),
  not whole statements by default.
- Viewing does not change where data may go: never commit it, write it into
  repository files, logs or docs, or send it to any other service (the
  project's privacy hooks and `CLAUDE.md` still apply).
- Rule 3 still applies to every other project and dataset. The user may
  narrow or withdraw this exception at any time.
