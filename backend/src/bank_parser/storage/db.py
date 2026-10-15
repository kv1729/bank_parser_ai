"""
SQLite persistence (stdlib sqlite3, WAL mode).

Statement JSON is stored per extraction; it contains personal data, so the
database lives in the gitignored data directory. Passwords are never stored.
"""
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
"""


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        with self.connect() as c:
            c.executescript(SCHEMA)
            # Additive migration (D-021): outputs the reviewer marks as printed but not extracted.
            if "missing_json" not in {r["name"] for r in c.execute("PRAGMA table_info(reviews)")}:
                c.execute("ALTER TABLE reviews ADD COLUMN missing_json TEXT NOT NULL DEFAULT '[]'")

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
    def add_extraction(self, doc_id, workflow, strategy, template_id, report, statement, problems, timings):
        with self.connect() as c:
            cur = c.execute(
                """INSERT INTO extractions(document_id, workflow, strategy, template_id, validation_status, verified,
                   report_json, statement_json, problems_json, timings_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, workflow, strategy, template_id, report["status"], int(report["verified"]),
                 json.dumps(report), json.dumps(statement) if statement is not None else None,
                 json.dumps(problems), json.dumps(timings), utcnow()))
            return cur.lastrowid

    def latest_extraction(self, doc_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM extractions WHERE document_id = ? ORDER BY id DESC LIMIT 1",
                            (doc_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        for k in ("report_json", "statement_json", "problems_json", "timings_json"):
            out[k[:-5]] = json.loads(out.pop(k)) if out[k] else None
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
    def save_review(self, doc_id, extraction_id, flags, remarks, missing=()):
        """One review per (document, extraction); saving again updates it in place.
        `flags`: outputs marked wrong; `missing`: outputs printed on the statement but not extracted."""
        now = utcnow()
        with self.connect() as c:
            c.execute("""INSERT INTO reviews(document_id, extraction_id, flags_json, missing_json, remarks,
                                             created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(document_id, extraction_id) DO UPDATE SET
                           flags_json = excluded.flags_json, missing_json = excluded.missing_json,
                           remarks = excluded.remarks, updated_at = excluded.updated_at""",
                      (doc_id, extraction_id, json.dumps(sorted(set(flags))), json.dumps(sorted(set(missing))),
                       remarks, now, now))
        return self.get_review(doc_id, extraction_id)

    @staticmethod
    def _review_row(row):
        if not row:
            return None
        out = dict(row)
        out["flags"] = json.loads(out.pop("flags_json"))
        out["missing"] = json.loads(out.pop("missing_json") or "[]")
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
        out = dict(row)
        for k in ("report_json", "statement_json", "problems_json", "timings_json"):
            out[k[:-5]] = json.loads(out.pop(k)) if out[k] else None
        return out

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

    def review_counts(self):
        """{document_id: number of outputs marked wrong or missing} for each document's latest review."""
        with self.connect() as c:
            rows = c.execute("""SELECT document_id, flags_json, missing_json, updated_at FROM reviews
                                ORDER BY updated_at""").fetchall()
        return {r["document_id"]: len(json.loads(r["flags_json"])) + len(json.loads(r["missing_json"] or "[]"))
                for r in rows}

