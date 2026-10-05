"""Shared paths and helpers for tests. Only synthetic samples are referenced here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # backend/
REPO_ROOT = ROOT.parent
SBI_PDF = ROOT / "tests" / "fixtures" / "pdfs" / "sbi_synthetic.pdf"
HDFC_PDF = ROOT / "tests" / "fixtures" / "pdfs" / "hdfc_synthetic.pdf"
COMMITTED_TEMPLATES = ROOT / "template_registry"
SBI_LAYOUT = "sbi/layout-5fbb871c"
HDFC_LAYOUT = "hdfc/layout-f2e8110d"

# Local-only real statement (gitignored sample_data/ at the repository root). Tests using it skip when absent.
REAL_PDF = REPO_ROOT / "sample_data" / "AccountStatement_02102026_120158.pdf"
REAL_PASSWORD_FILE = REPO_ROOT / "sample_data" / ".pdf_password"


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


BACKENDS = ["pypdfium2", "pdfplumber"] + (["pymupdf"] if have_pymupdf() else [])
