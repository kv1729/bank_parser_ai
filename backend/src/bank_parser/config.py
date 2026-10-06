"""
Runtime settings (environment variables, all optional).

There is deliberately no document-size cap (DECISIONS.md D-012). Resource
protection is about *how* work runs -- bounded worker pools, page-at-a-time
processing, streaming uploads, wall-clock limits -- not about rejecting
large statements. `BANK_PARSER_MAX_UPLOAD_BYTES` exists only as an optional
infrastructure guard and is unset (unlimited) by default.
"""
from dataclasses import dataclass
import os
from pathlib import Path

# backend/src/bank_parser/config.py -> parents[2] = backend/, parents[3] = repository root
BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def _int(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _float(name, default):
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    committed_templates_dir: Path
    frontend_dir: Path = REPO_ROOT / "frontend"
    private_samples_dir: Path = REPO_ROOT / "sample_data"           # gitignored private statements
    synthetic_samples_dir: Path = BACKEND_ROOT / "tests" / "fixtures" / "pdfs"
    pdf_backend: str = "pypdfium2"      # DECISIONS.md D-015
    extraction_workers: int = 2
    learning_workers: int = 1
    learning_max_seconds: float = 300.0
    learning_max_candidates: int = 3
    learning_max_refinements: int = 2
    upload_chunk_bytes: int = 1024 * 1024
    max_upload_bytes: int | None = None     # None = no limit (infrastructure guard only)

    @property
    def documents_dir(self):
        return self.data_dir / "documents"

    @property
    def learned_templates_dir(self):
        return self.data_dir / "templates"

    @property
    def db_path(self):
        return self.data_dir / "bank_parser.db"

    @property
    def tmp_dir(self):
        return self.data_dir / "tmp"

    @classmethod
    def from_env(cls, **overrides):
        values = dict(
            data_dir=Path(os.environ.get("BANK_PARSER_DATA_DIR", REPO_ROOT / "data")),
            committed_templates_dir=Path(os.environ.get("BANK_PARSER_TEMPLATES_DIR", BACKEND_ROOT / "template_registry")),
            frontend_dir=Path(os.environ.get("BANK_PARSER_FRONTEND_DIR", REPO_ROOT / "frontend")),
            private_samples_dir=Path(os.environ.get("BANK_PARSER_PRIVATE_SAMPLES_DIR", REPO_ROOT / "sample_data")),
            pdf_backend=os.environ.get("BANK_PARSER_PDF_BACKEND", "pypdfium2"),
            extraction_workers=_int("BANK_PARSER_EXTRACTION_WORKERS", 2),
            learning_workers=_int("BANK_PARSER_LEARNING_WORKERS", 1),
            learning_max_seconds=_float("BANK_PARSER_LEARNING_MAX_SECONDS", 300.0),
            learning_max_candidates=_int("BANK_PARSER_LEARNING_MAX_CANDIDATES", 3),
            learning_max_refinements=_int("BANK_PARSER_LEARNING_MAX_REFINEMENTS", 2),
            upload_chunk_bytes=_int("BANK_PARSER_UPLOAD_CHUNK_BYTES", 1024 * 1024),
            max_upload_bytes=_int("BANK_PARSER_MAX_UPLOAD_BYTES", None),
        )
        values.update(overrides)
        return cls(**values)
