"""
Backend-neutral view of a PDF page: words with coordinates, ruled rectangles
and line segments. Extraction code depends only on these types, so the PDF
library can be swapped (DECISIONS.md D-007, D-015). Default: pypdfium2.

Coordinates are PDF points with the origin at the top-left of the page.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field


class PdfError(Exception):
    """Base class for PDF problems that should be reported, not crashed on."""


class PasswordRequired(PdfError):
    """The PDF is encrypted and no password (or a wrong one) was supplied."""


class MalformedPdf(PdfError):
    """The file could not be parsed as a PDF."""


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def x_mid(self):
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class Rect:
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class PageLayout:
    number: int                     # 1-based
    width: float
    height: float
    words: tuple[Word, ...]
    rects: tuple[Rect, ...] = field(default_factory=tuple)
    image_count: int = 0

    @property
    def has_text(self):
        return any(w.text.strip() for w in self.words)


class PdfDocument:
    """A PDF opened by a backend. Pages are produced lazily, one at a time."""

    backend = "abstract"

    @property
    def page_count(self):
        raise NotImplementedError

    def page(self, number, with_rects=False):
        """Return the PageLayout for a 1-based page number. Ruled rectangles are
        collected only on request: extraction does not need them and on heavily
        ruled pages they cost more than the words."""
        raise NotImplementedError

    def iter_pages(self, numbers=None, with_rects=False):
        for n in numbers or range(1, self.page_count + 1):
            yield self.page(n, with_rects)

    def find_table_cells(self, number):
        """Cell rectangles of ruled tables on a page (used only for layout learning)."""
        return ()

    def close(self):
        pass


def available_backends():
    names = ["pypdfium2", "pdfplumber"]
    try:
        import pymupdf  # noqa: F401
        names.append("pymupdf")
    except ImportError:
        pass
    return names


@contextmanager
def open_pdf(path, password=None, backend="pypdfium2"):
    if backend == "pypdfium2":
        from bank_parser.pdf.pypdfium2_backend import Pypdfium2Document as cls
    elif backend == "pdfplumber":
        from bank_parser.pdf.pdfplumber_backend import PdfplumberDocument as cls
    elif backend == "pymupdf":
        from bank_parser.pdf.pymupdf_backend import PymupdfDocument as cls
    else:
        raise ValueError(f"unknown PDF backend {backend!r}")
    doc = cls(path, password)
    try:
        yield doc
    finally:
        doc.close()
