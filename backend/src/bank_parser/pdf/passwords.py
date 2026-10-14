"""
Local password files for private statements.

`sample_data/.pdf_password` holds one password per line (statements from
different banks or accounts usually have different passwords). Passwords are
only ever used in memory to open a PDF; never print, log or store them.
"""
from pathlib import Path

from bank_parser.pdf.backend import PdfError, open_pdf


def read_password_file(path):
    """Non-empty lines of a password file, in order ([] if the file is missing)."""
    path = Path(path)
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_password(pdf_path, candidates):
    """
    The candidate that opens `pdf_path`, or None when the PDF needs no password
    or none of the candidates works (the caller then reports NEEDS_PASSWORD).
    """
    for password in [None, *candidates]:
        try:
            with open_pdf(pdf_path, password):
                return password
        except PdfError:
            continue
    return None
