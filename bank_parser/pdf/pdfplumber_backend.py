import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from bank_parser.pdf.backend import MalformedPdf, PageLayout, PasswordRequired, PdfDocument, Rect, Word


class PdfplumberDocument(PdfDocument):
    backend = "pdfplumber"

    def __init__(self, path, password=None):
        try:
            self._pdf = pdfplumber.open(path, password=password or "")
            self._count = len(self._pdf.pages)
        except PDFPasswordIncorrect:
            raise PasswordRequired("PDF is encrypted; a correct password is required") from None
        except Exception as e:  # pdfminer raises many unrelated types for bad input
            raise MalformedPdf(f"could not parse PDF: {type(e).__name__}") from None

    @property
    def page_count(self):
        return self._count

    def page(self, number, with_rects=False):
        pg = self._pdf.pages[number - 1]
        try:
            words = tuple(
                Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"])
                for w in pg.extract_words(keep_blank_chars=False, use_text_flow=False)
            )
            rects = tuple(Rect(r["x0"], r["x1"], r["top"], r["bottom"]) for r in pg.rects) if with_rects else ()
            return PageLayout(number, float(pg.width), float(pg.height), words, rects, len(pg.images))
        finally:
            pg.close()  # flush pdfplumber's per-page caches: keeps memory flat on large files

    def find_table_cells(self, number):
        pg = self._pdf.pages[number - 1]
        try:
            cells = []
            for t in pg.find_tables():
                for row in t.rows:
                    cells.append(tuple(c for c in row.cells if c))
            return tuple(cells)
        finally:
            pg.close()

    def close(self):
        self._pdf.close()
