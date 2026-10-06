"""Render PDF pages to PNG for the review screen (pypdfium2; one page at a time)."""
import io

import pypdfium2 as pdfium

from bank_parser.pdf.backend import MalformedPdf, PasswordRequired
from bank_parser.pdf.pdfium_lock import PDFIUM_LOCK

_PASSWORD_ERROR = 4
MAX_SCALE = 3.0


def render_page_png(path, number, password=None, scale=1.5):
    """PNG bytes of a 1-based page. Raises PasswordRequired, MalformedPdf or IndexError."""
    scale = max(0.5, min(float(scale), MAX_SCALE))
    with PDFIUM_LOCK:              # PDFium is not thread-safe (pdfium_lock.py)
        image = _render(path, number, password, scale)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)   # PNG encoding needs no lock
    return out.getvalue()


def _render(path, number, password, scale):
    try:
        doc = pdfium.PdfDocument(str(path), password=password or None)
    except pdfium.PdfiumError as e:
        if getattr(e, "err_code", None) == _PASSWORD_ERROR:
            raise PasswordRequired("PDF is encrypted; a correct password is required") from None
        raise MalformedPdf(f"could not parse PDF: {e}") from None
    try:
        if not 1 <= number <= len(doc):
            raise IndexError(f"page {number} out of range 1..{len(doc)}")
        page = doc[number - 1]
        try:
            return page.render(scale=scale).to_pil().copy()   # own the pixels before PDFium frees the bitmap
        finally:
            page.close()
    finally:
        doc.close()
