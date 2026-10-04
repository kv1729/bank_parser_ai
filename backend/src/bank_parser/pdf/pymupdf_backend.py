"""
Optional PyMuPDF backend: ~15x faster page parsing than pdfplumber, but
PyMuPDF is AGPL-3.0 licensed (DECISIONS.md D-007). Not used unless selected.
"""
import pymupdf

from bank_parser.pdf.backend import MalformedPdf, PageLayout, PasswordRequired, PdfDocument, Rect, Word


class PymupdfDocument(PdfDocument):
    backend = "pymupdf"

    def __init__(self, path, password=None):
        try:
            self._doc = pymupdf.open(path)
        except Exception as e:  # pymupdf raises its own FileDataError etc.
            raise MalformedPdf(f"could not parse PDF: {type(e).__name__}") from None
        if not self._doc.is_pdf:  # pymupdf also opens images, text files, ebooks...
            self._doc.close()
            raise MalformedPdf("not a PDF document")
        if self._doc.needs_pass and not self._doc.authenticate(password or ""):
            self._doc.close()
            raise PasswordRequired("PDF is encrypted; a correct password is required")

    @property
    def page_count(self):
        return self._doc.page_count

    def page(self, number, with_rects=False):
        pg = self._doc[number - 1]
        words = tuple(Word(w[4], w[0], w[2], w[1], w[3]) for w in pg.get_text("words"))
        rects = []
        for d in (pg.get_drawings() if with_rects else ()):
            for item in d["items"]:
                if item[0] == "re":
                    r = item[1]
                    rects.append(Rect(r.x0, r.x1, r.y0, r.y1))
        return PageLayout(number, float(pg.rect.width), float(pg.rect.height), words,
                          tuple(rects), len(pg.get_images()))

    def close(self):
        self._doc.close()
