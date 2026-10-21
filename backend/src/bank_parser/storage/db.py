"""
SQLite persistence (stdlib sqlite3, WAL mode).

Statement JSON is stored per extraction; it contains personal data, so the
database lives in the gitignored data directory. Passwords are never stored.
"""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,           -- sha256 of file content
    original_name   TEXT,
    size_bytes      INTEGER NOT NULL,
    page_count      INTEGER,
    pdf_kind        TEXT,
    encrypted       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extractions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id       TEXT NOT NULL REFERENCES documents(id),
    workflow          TEXT NOT NULL,            -- known_template | unknown_layout
    strategy          TEXT NOT NULL,
    template_id       TEXT,
    validation_status TEXT NOT NULL,
    verified          INTEGER NOT NULL,
    report_json       TEXT NOT NULL,
    statement_json    TEXT,
    problems_json     TEXT NOT NULL,
    timings_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS extractions_document ON extractions(document_id);
CREATE TABLE IF NOT EXISTS template_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   TEXT NOT NULL REFERENCES documents(id),
    status        TEXT NOT NULL,               -- QUEUED | RUNNING | ACCEPTED | HUMAN_REVIEW | FAILED
    template_id   TEXT,
    reason        TEXT,
    attempts_json TEXT,
    duration_ms   REAL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS template_jobs_document ON template_jobs(document_id);
CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   TEXT NOT NULL REFERENCES documents(id),
    extraction_id INTEGER NOT NULL REFERENCES extractions(id),
    flags_json    TEXT NOT NULL,              -- keys of outputs the reviewer marked incorrect
    remarks       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (document_id, extraction_id)
);
-- Golden statements (Stage 9, D-022): approved output snapshots. Append-only:
-- re-approval adds a version; nothing is updated in place except retiring.
CREATE TABLE IF NOT EXISTS golden_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id       TEXT NOT NULL REFERENCES documents(id),
    version           INTEGER NOT NULL,
    extraction_id     INTEGER NOT NULL REFERENCES extractions(id),
    snapshot_json     TEXT NOT NULL,            -- statement without provenance
    workflow          TEXT NOT NULL,            -- known_template | unknown_layout
    template_id       TEXT,
    backend           TEXT NOT NULL,
    engine_commit     TEXT,
    validation_status TEXT NOT NULL,
    warnings_json     TEXT NOT NULL,            -- warning codes the owner acknowledged
    password_source   TEXT NOT NULL,            -- none | password_file (the password is never stored)
    status            TEXT NOT NULL DEFAULT 'active',
    approved_at       TEXT NOT NULL,
    UNIQUE (document_id, version)
);
-- Training examples (Stage 10.7, D-027): the owner's approvals and corrections in a form the
-- template agent and the self-review use. Append-only; never statement values, only heading
-- words, roles, field names, counts and verdicts. `example_key` makes re-recording a no-op.
CREATE TABLE IF NOT EXISTS training_examples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,               -- approval | table_roles | field_correction | verdict
    document_id   TEXT NOT NULL REFERENCES documents(id),
    template_id   TEXT,
    source        TEXT NOT NULL,               -- golden | review
    payload_json  TEXT NOT NULL,
    example_key   TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL,
    occurred_at   TEXT                         -- when the approval or review happened (orders feedback)
);
"""


def layout_of(template_id):
    """'boi/layout-23f4f42a/v3' -> 'boi/layout-23f4f42a': feedback applies to every version of a layout."""
    return template_id.rsplit("/", 1)[0] if template_id and template_id.count("/") >= 2 else template_id


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        with self.connect() as c:
            c.executescript(SCHEMA)
            # Additive migration (D-021): outputs the reviewer marks as printed but not extracted.
            review_cols = {r["name"] for r in c.execute("PRAGMA table_info(reviews)")}
            if "missing_json" not in review_cols:
                c.execute("ALTER TABLE reviews ADD COLUMN missing_json TEXT NOT NULL DEFAULT '[]'")
            # Additive migrations (D-024): the system's self-review, and the owner's verdicts on it.
            if "agent_feedback_json" not in review_cols:
                c.execute("ALTER TABLE reviews ADD COLUMN agent_feedback_json TEXT")
            if "self_review_json" not in {r["name"] for r in c.execute("PRAGMA table_info(extractions)")}:
                c.execute("ALTER TABLE extractions ADD COLUMN self_review_json TEXT")
            if "occurred_at" not in {r["name"] for r in c.execute("PRAGMA table_info(training_examples)")}:
                c.execute("ALTER TABLE training_examples ADD COLUMN occurred_at TEXT")

    @contextmanager
    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        yield conn

    # --- documents ----------------------------------------------------------
    def upsert_document(self, doc_id, original_name, size_bytes):
        now = utcnow()
        with self.connect() as c:
            c.execute("""INSERT INTO documents(id, original_name, size_bytes, status, created_at, updated_at)
                         VALUES (?, ?, ?, 'RECEIVED', ?, ?) ON CONFLICT(id) DO NOTHING""",
                      (doc_id, original_name, size_bytes, now, now))

    def update_document(self, doc_id, **fields):
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))

    def mark_interrupted(self):
        """At app start: work that was running when the app stopped will never finish (jobs do not
        survive a restart, CHECKPOINT G4). Mark it so the statement can be processed again.
        Only status fields change; extractions and reviews are kept. Returns the documents marked."""
        now = utcnow()
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM documents WHERE status IN ('PROCESSING', 'LEARNING')")]
            c.execute("""UPDATE documents SET status = 'NEEDS_REVIEW', updated_at = ?,
                         error = 'interrupted: the app stopped while this statement was being processed or learned; reprocess it'
                         WHERE status IN ('PROCESSING', 'LEARNING')""", (now,))
            c.execute("""UPDATE template_jobs SET status = 'FAILED', reason = 'interrupted: the app stopped', updated_at = ?
                         WHERE status IN ('QUEUED', 'RUNNING')""", (now,))
        return ids

    def get_document(self, doc_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, limit=100):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- extractions --------------------------------------------------------
    def add_extraction(self, doc_id, workflow, strategy, template_id, report, statement, problems, timings,
                       self_review=None):
        with self.connect() as c:
            cur = c.execute(
                """INSERT INTO extractions(document_id, workflow, strategy, template_id, validation_status, verified,
                   report_json, statement_json, problems_json, timings_json, created_at, self_review_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, workflow, strategy, template_id, report["status"], int(report["verified"]),
                 json.dumps(report), json.dumps(statement) if statement is not None else None,
                 json.dumps(problems), json.dumps(timings), utcnow(),
                 json.dumps(self_review) if self_review is not None else None))
            return cur.lastrowid

    def latest_extraction(self, doc_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM extractions WHERE document_id = ? ORDER BY id DESC LIMIT 1",
                            (doc_id,)).fetchone()
        if not row:
            return None
        return self._extraction_row(row)

    @staticmethod
    def _extraction_row(row):
        out = dict(row)
        for k in ("report_json", "statement_json", "problems_json", "timings_json", "self_review_json"):
            out[k[:-5]] = json.loads(out.pop(k)) if out.get(k) else None
        return out

    def verified_documents(self):
        """Documents with a verified extraction by a template: the regression corpus."""
        with self.connect() as c:
            rows = c.execute("""SELECT d.id, d.encrypted, e.template_id FROM documents d
                                JOIN extractions e ON e.document_id = d.id
                                WHERE e.verified = 1 AND e.template_id IS NOT NULL
                                GROUP BY d.id""").fetchall()
        return [dict(r) for r in rows]

    # --- template jobs ------------------------------------------------------
    def add_template_job(self, doc_id):
        now = utcnow()
        with self.connect() as c:
            cur = c.execute("""INSERT INTO template_jobs(document_id, status, created_at, updated_at)
                               VALUES (?, 'QUEUED', ?, ?)""", (doc_id, now, now))
            return cur.lastrowid

    def update_template_job(self, job_id, **fields):
        if "attempts" in fields:
            fields["attempts_json"] = json.dumps(fields.pop("attempts"))
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE template_jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def latest_template_job(self, doc_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM template_jobs WHERE document_id = ? ORDER BY id DESC LIMIT 1",
                            (doc_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["attempts"] = json.loads(out.pop("attempts_json")) if out.get("attempts_json") else []
        return out

    # --- human review -------------------------------------------------------------
    def save_review(self, doc_id, extraction_id, flags, remarks, missing=(), agent_feedback=None):
        """One review per (document, extraction); saving again updates it in place.
        `flags`: outputs marked wrong; `missing`: outputs printed on the statement but not extracted;
        `agent_feedback`: the owner's verdicts on the self-review (None keeps the stored ones)."""
        now = utcnow()
        feedback = json.dumps(agent_feedback, sort_keys=True) if agent_feedback is not None else None
        with self.connect() as c:
            c.execute("""INSERT INTO reviews(document_id, extraction_id, flags_json, missing_json, remarks,
                                             created_at, updated_at, agent_feedback_json)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(document_id, extraction_id) DO UPDATE SET
                           flags_json = excluded.flags_json, missing_json = excluded.missing_json,
                           remarks = excluded.remarks, updated_at = excluded.updated_at,
                           agent_feedback_json = COALESCE(excluded.agent_feedback_json, reviews.agent_feedback_json)""",
                      (doc_id, extraction_id, json.dumps(sorted(set(flags))), json.dumps(sorted(set(missing))),
                       remarks, now, now, feedback))
        return self.get_review(doc_id, extraction_id)

    @staticmethod
    def _review_row(row):
        if not row:
            return None
        out = dict(row)
        out["flags"] = json.loads(out.pop("flags_json"))
        out["missing"] = json.loads(out.pop("missing_json") or "[]")
        out["agent_feedback"] = json.loads(out.pop("agent_feedback_json", None) or "{}")
        return out

    def get_review(self, doc_id, extraction_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM reviews WHERE document_id = ? AND extraction_id = ?",
                            (doc_id, extraction_id)).fetchone()
        return self._review_row(row)

    def previous_review(self, doc_id, extraction_id):
        """The newest review made on an earlier extraction of the document (kept when it is re-extracted)."""
        with self.connect() as c:
            row = c.execute("""SELECT * FROM reviews WHERE document_id = ? AND extraction_id < ?
                               ORDER BY extraction_id DESC LIMIT 1""", (doc_id, extraction_id)).fetchone()
        return self._review_row(row)

    def get_extraction(self, extraction_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM extractions WHERE id = ?", (extraction_id,)).fetchone()
        if not row:
            return None
        return self._extraction_row(row)

    # --- golden statements ----------------------------------------------------------
    def add_golden(self, doc_id, extraction_id, snapshot, workflow, template_id, backend, engine_commit,
                   validation_status, warnings, password_source):
        """Store a new golden version for the document (never replaces an older one)."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM golden_snapshots WHERE document_id = ?",
                                    (doc_id,)).fetchone()[0]
                c.execute("""INSERT INTO golden_snapshots(document_id, version, extraction_id, snapshot_json, workflow,
                               template_id, backend, engine_commit, validation_status, warnings_json, password_source,
                               approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (doc_id, version, extraction_id, json.dumps(snapshot, sort_keys=True), workflow, template_id,
                           backend, engine_commit, validation_status, json.dumps(sorted(set(warnings))),
                           password_source, utcnow()))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return self.golden_for(doc_id)

    @staticmethod
    def _golden_row(row):
        if not row:
            return None
        out = dict(row)
        out["snapshot"] = json.loads(out.pop("snapshot_json"))
        out["warnings"] = json.loads(out.pop("warnings_json"))
        return out

    def golden_for(self, doc_id):
        """The document's active golden snapshot (newest version), or None."""
        with self.connect() as c:
            row = c.execute("""SELECT * FROM golden_snapshots WHERE document_id = ? AND status = 'active'
                               ORDER BY version DESC LIMIT 1""", (doc_id,)).fetchone()
        return self._golden_row(row)

    def active_goldens(self):
        """The active (newest) golden snapshot of every golden document."""
        with self.connect() as c:
            rows = c.execute("""SELECT g.* FROM golden_snapshots g
                                WHERE g.status = 'active' AND g.version = (
                                    SELECT MAX(version) FROM golden_snapshots
                                    WHERE document_id = g.document_id AND status = 'active')
                                ORDER BY g.document_id""").fetchall()
        return [self._golden_row(r) for r in rows]

    def golden_versions(self):
        """{document_id: active golden version} for the review list."""
        return {g["document_id"]: g["version"] for g in self.active_goldens()}

    # --- training examples (Stage 10.7, D-027) ------------------------------------------
    def add_example(self, kind, document_id, template_id, source, payload, occurred_at=None):
        """Record one example; the same (kind, document, template, source, payload) again is a no-op,
        except that a missing `occurred_at` is filled in. Returns True when a new row was added.
        `occurred_at`: when the approval or review happened (default now); feedback is ordered by it."""
        body = json.dumps(payload, sort_keys=True)
        key = hashlib.sha256("|".join((kind, document_id, template_id or "", source, body)).encode()).hexdigest()
        now = utcnow()
        with self.connect() as c:
            if c.execute("SELECT 1 FROM training_examples WHERE example_key = ?", (key,)).fetchone():
                c.execute("UPDATE training_examples SET occurred_at = ? WHERE example_key = ? AND occurred_at IS NULL",
                          (occurred_at or now, key))
                return False
            c.execute("""INSERT OR IGNORE INTO training_examples(kind, document_id, template_id, source, payload_json,
                         example_key, created_at, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                      (kind, document_id, template_id, source, body, key, now, occurred_at or now))
            return True

    def examples(self, kind=None):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM training_examples" + (" WHERE kind = ?" if kind else "") + " ORDER BY id",
                             (kind,) if kind else ()).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            d.pop("example_key")
            out.append(d)
        return out

    def example_counts(self):
        with self.connect() as c:
            return {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM training_examples GROUP BY kind")}

    def example_history(self, template_id):
        """The owner's earlier feedback on this template's layout (any version), for the self-review:
        corrections made since the layout's latest golden approval, and every verdict."""
        layout = layout_of(template_id)
        if not layout:
            return None
        # Ordered by when the approval or review happened (a backfill records old events late);
        # same second: by the order they were recorded.
        when = "COALESCE(occurred_at, created_at)"
        with self.connect() as c:
            last = c.execute(f"""SELECT {when} AS t, id FROM training_examples WHERE kind = 'approval'
                                 AND (template_id = ? OR template_id LIKE ?) ORDER BY t DESC, id DESC LIMIT 1""",
                             (layout, layout + "/%")).fetchone()
            t, i = (last["t"], last["id"]) if last else ("", 0)
            rows = c.execute(f"""SELECT kind, payload_json FROM training_examples
                                 WHERE kind IN ('field_correction', 'verdict') AND (template_id = ? OR template_id LIKE ?)
                                 AND (kind = 'verdict' OR {when} > ? OR ({when} = ? AND id > ?))
                                 ORDER BY {when}, id""",
                             (layout, layout + "/%", t, t, i)).fetchall()
        return [{"kind": r["kind"], **json.loads(r["payload_json"])} for r in rows]

    def review_counts(self):
        """{document_id: number of outputs marked wrong or missing} for each document's latest review."""
        with self.connect() as c:
            rows = c.execute("""SELECT document_id, flags_json, missing_json, updated_at FROM reviews
                                ORDER BY updated_at""").fetchall()
        return {r["document_id"]: len(json.loads(r["flags_json"])) + len(json.loads(r["missing_json"] or "[]"))
                for r in rows}

