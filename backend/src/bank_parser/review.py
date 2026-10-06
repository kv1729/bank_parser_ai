"""
Human review of extractions (Stage 7).

- Lists the PDFs available for review: local folders (private statements in
  sample_data/, synthetic samples) and anything already uploaded.
- Imports a local file on selection (content-addressed, so importing twice is
  a no-op) and queues it for processing.
- Keeps PDF passwords in memory only, for a limited time, so encrypted pages
  can be rendered for review. Passwords are never written anywhere.
- Validates what a reviewer may flag: header fields, printed totals and
  individual transaction rows of one specific extraction.
"""
from dataclasses import dataclass, fields
import hashlib
import threading
import time
from pathlib import Path

from bank_parser.core.schema import StatementHeader, StatementSummary

PASSWORD_TTL_SECONDS = 4 * 3600
MAX_REMARKS_CHARS = 20_000

HEADER_KEYS = tuple(f"header.{f.name}" for f in fields(StatementHeader) if f.name != "extra")
SUMMARY_KEYS = tuple(f"summary.{f.name}" for f in fields(StatementSummary))


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDir:
    key: str          # stable id used in URLs, e.g. "private"
    label: str        # shown in the UI
    path: Path


class PasswordVault:
    """In-memory, time-limited password store keyed by document id."""

    def __init__(self, ttl=PASSWORD_TTL_SECONDS):
        self.ttl = ttl
        self._items = {}
        self._lock = threading.Lock()

    def put(self, document_id, password):
        if password:
            with self._lock:
                self._items[document_id] = (password, time.monotonic() + self.ttl)

    def get(self, document_id):
        with self._lock:
            item = self._items.get(document_id)
            if item and item[1] < time.monotonic():
                del self._items[document_id]
                item = None
        return item[0] if item else None


class ReviewSources:
    def __init__(self, dirs):
        self.dirs = {d.key: d for d in dirs}
        self._hash_cache = {}      # (path, size, mtime_ns) -> sha256

    def _sha256(self, path):
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
        if key not in self._hash_cache:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            self._hash_cache[key] = h.hexdigest()
        return self._hash_cache[key]

    def list(self):
        out = []
        for d in self.dirs.values():
            if not d.path.is_dir():
                continue
            for p in sorted(d.path.glob("*.pdf"), key=lambda p: p.name.lower()):
                if p.is_file():
                    out.append({"source_id": f"{d.key}:{p.name}", "group": d.label, "name": p.name,
                                "size_bytes": p.stat().st_size, "document_id": self._sha256(p)})
        return out

    def resolve(self, source_id):
        """Map a source id back to a file. Only names that are actually listed resolve."""
        key, _, name = source_id.partition(":")
        d = self.dirs.get(key)
        if d is None or not name or "/" in name or "\\" in name or name.startswith("."):
            raise ReviewError("unknown source")
        path = d.path / name
        if not path.is_file() or path.suffix.lower() != ".pdf" or path.resolve().parent != d.path.resolve():
            raise ReviewError("unknown source")
        return d, path

    @staticmethod
    def default_password(source_dir):
        """A folder may hold its statements' password in a local `.pdf_password` file."""
        f = source_dir.path / ".pdf_password"
        return f.read_text(encoding="utf-8").strip() or None if f.is_file() else None


def validate_review(statement, flags, remarks):
    """Return (flags, remarks) normalised, or raise ReviewError."""
    if not isinstance(flags, list) or not all(isinstance(k, str) for k in flags):
        raise ReviewError("flags must be a list of strings")
    if not isinstance(remarks, str):
        raise ReviewError("remarks must be a string")
    if len(remarks) > MAX_REMARKS_CHARS:
        raise ReviewError(f"remarks longer than {MAX_REMARKS_CHARS} characters")
    rows = len((statement or {}).get("transactions") or [])
    allowed = set(HEADER_KEYS) | set(SUMMARY_KEYS)
    for k in flags:
        if k in allowed:
            continue
        if k.startswith("txn.") and k[4:].isdigit() and int(k[4:]) < rows:
            continue
        raise ReviewError(f"unknown output key {k!r}")
    return sorted(set(flags)), remarks
