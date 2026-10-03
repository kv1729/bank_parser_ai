"""Shared paths and helpers for tests. Only synthetic samples are referenced here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SBI_PDF = ROOT / "sample_data" / "SBI_Bank_Statement_Chenna_Reddy.pdf"
HDFC_PDF = ROOT / "sample_data" / "sample_statement.pdf"
COMMITTED_TEMPLATES = ROOT / "template_registry"
SBI_LAYOUT = "sbi/layout-5fbb871c"
HDFC_LAYOUT = "hdfc/layout-f2e8110d"

# Local-only real statement (gitignored). Tests using it skip when absent.
REAL_PDF = ROOT / "sample_data" / "AccountStatement_02102026_120158.pdf"
REAL_PASSWORD_FILE = ROOT / "sample_data" / ".pdf_password"


def real_statement():
    if REAL_PDF.exists() and REAL_PASSWORD_FILE.exists():
        return REAL_PDF, REAL_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    return None, None


def have_pymupdf():
    try:
        import pymupdf  # noqa: F401
        return True
    except ImportError:
        return False


BACKENDS = ["pdfplumber"] + (["pymupdf"] if have_pymupdf() else [])
