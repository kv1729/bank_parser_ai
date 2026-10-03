"""
Content-addressed document storage.

Uploads are streamed to a temporary file in fixed-size chunks while the
SHA-256 is computed, then atomically moved to <documents>/<sha256>.pdf. The
hash is the document ID: re-uploading the same file is idempotent, and no
two documents can ever share a cache entry (fixes the prototype's fixed
cache path; DECISIONS.md D-012).
"""
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class UploadTooLarge(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    document_id: str
    path: Path
    size_bytes: int
    already_existed: bool


class DocumentStore:
    def __init__(self, documents_dir, tmp_dir, chunk_bytes=1024 * 1024, max_bytes=None):
        self.documents_dir = Path(documents_dir)
        self.tmp_dir = Path(tmp_dir)
        self.chunk_bytes = chunk_bytes
        self.max_bytes = max_bytes
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, document_id):
        if len(document_id) != 64 or any(c not in "0123456789abcdef" for c in document_id):
            raise ValueError("invalid document id")
        return self.documents_dir / f"{document_id}.pdf"

    def save_stream(self, read_chunk):
        """
        `read_chunk(n)` returns up to n bytes (b"" at end), e.g. a file's .read.
        Memory use is one chunk regardless of file size.
        """
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = read_chunk(self.chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if self.max_bytes is not None and size > self.max_bytes:
                        raise UploadTooLarge(f"upload exceeds configured limit of {self.max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
            document_id = digest.hexdigest()
            final = self.path_for(document_id)
            if final.exists():
                os.unlink(tmp_name)
                return StoredFile(document_id, final, size, True)
            os.replace(tmp_name, final)
            return StoredFile(document_id, final, size, False)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def save_path(self, path):
        with open(path, "rb") as f:
            return self.save_stream(f.read)
