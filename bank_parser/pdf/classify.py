"""Deterministic PDF classification: text-based, scanned, or mixed."""
from dataclasses import dataclass
from enum import Enum

from bank_parser.pdf.backend import open_pdf

# A page with fewer extractable characters than this is treated as having no
# usable text layer (blank or image-only) and is flagged for future OCR.
MIN_TEXT_CHARS = 20


class PdfKind(str, Enum):
    TEXT = "TEXT"          # every page has a text layer
    MIXED = "MIXED"        # some pages need OCR
    SCANNED = "SCANNED"    # no page has a text layer
    EMPTY = "EMPTY"        # zero pages


@dataclass(frozen=True)
class Classification:
    kind: PdfKind
    page_count: int
    text_pages: tuple[int, ...]
    needs_ocr_pages: tuple[int, ...]


def classify(path, password=None, backend="pdfplumber"):
    """Open the PDF (raises PasswordRequired / MalformedPdf) and classify its pages."""
    text_pages, ocr_pages = [], []
    with open_pdf(path, password, backend) as doc:
        for page in doc.iter_pages():
            chars = sum(len(w.text) for w in page.words)
            (text_pages if chars >= MIN_TEXT_CHARS else ocr_pages).append(page.number)
        count = doc.page_count
    if count == 0:
        kind = PdfKind.EMPTY
    elif not ocr_pages:
        kind = PdfKind.TEXT
    elif not text_pages:
        kind = PdfKind.SCANNED
    else:
        kind = PdfKind.MIXED
    return Classification(kind, count, tuple(text_pages), tuple(ocr_pages))
