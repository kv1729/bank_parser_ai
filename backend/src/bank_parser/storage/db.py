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
"""


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        with self.connect() as c:
            c.executescript(SCHEMA)

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
    def save_review(self, doc_id, extraction_id, flags, remarks):
        """One review per (document, extraction); saving again updates it in place."""
        now = utcnow()
        with self.connect() as c:
            c.execute("""INSERT INTO reviews(document_id, extraction_id, flags_json, remarks, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?)
                         ON CONFLICT(document_id, extraction_id) DO UPDATE SET
                           flags_json = excluded.flags_json, remarks = excluded.remarks,
                           updated_at = excluded.updated_at""",
                      (doc_id, extraction_id, json.dumps(sorted(set(flags))), remarks, now, now))
        return self.get_review(doc_id, extraction_id)

    def get_review(self, doc_id, extraction_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM reviews WHERE document_id = ? AND extraction_id = ?",
                            (doc_id, extraction_id)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["flags"] = json.loads(out.pop("flags_json"))
        return out

    def get_extraction(self, extraction_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM extractions WHERE id = ?", (extraction_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        for k in ("report_json", "statement_json", "problems_json", "timings_json"):
            out[k[:-5]] = json.loads(out.pop(k)) if out[k] else None
        return out

    def review_counts(self):
        """{document_id: number of flagged outputs} for each document's latest review."""
        with self.connect() as c:
            rows = c.execute("""SELECT document_id, flags_json, updated_at FROM reviews
                                ORDER BY updated_at""").fetchall()
        return {r["document_id"]: len(json.loads(r["flags_json"])) for r in rows}

