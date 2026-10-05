"""
pypdfium2 backend (default): Google's PDFium engine. Apache-2.0 / BSD-3
licensed and already a dependency of pdfplumber. About 15x faster than
pdfplumber at producing positioned words, with the same words and
coordinates (DECISIONS.md D-015).

Words are runs of non-whitespace characters; PDFium inserts spaces where it
detects word gaps, so no extra gap heuristic is needed. Word boxes use
PDFium's *loose* character boxes (font ascent/descent), which are the same
height for every glyph of a font size -- tight glyph boxes would differ
between "T" and "a" and could split one printed line into two.
"""
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from bank_parser.pdf.backend import MalformedPdf, PageLayout, PasswordRequired, PdfDocument, Rect, Word

_PASSWORD_ERROR = 4    # FPDF_ERR_PASSWORD


class Pypdfium2Document(PdfDocument):
    backend = "pypdfium2"

    def __init__(self, path, password=None):
        try:
            self._doc = pdfium.PdfDocument(str(path), password=password or None)
        except pdfium.PdfiumError as e:
            if getattr(e, "err_code", None) == _PASSWORD_ERROR:
                raise PasswordRequired("PDF is encrypted; a correct password is required") from None
            raise MalformedPdf(f"could not parse PDF: {e}") from None
        except (OSError, ValueError, TypeError) as e:
            raise MalformedPdf(f"could not parse PDF: {type(e).__name__}") from None

    @property
    def page_count(self):
        return len(self._doc)

    def page(self, number, with_rects=False):
        page = self._doc[number - 1]
        try:
            left, bottom, right, top = page.get_bbox()
            width, height = right - left, top - bottom
            textpage = page.get_textpage()
            try:
                words = self._words(textpage, left, top)
            finally:
                textpage.close()
            rects, images = [], 0
            for obj in page.get_objects():
                if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    images += 1
                elif with_rects and obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
                    l, b, r, t = obj.get_pos()
                    rects.append(Rect(l - left, r - left, top - t, top - b))
            return PageLayout(number, float(width), float(height), words, tuple(rects), images)
        finally:
            page.close()

    @staticmethod
    def _words(textpage, left, top):
        count = textpage.count_chars()
        text = textpage.get_text_range(0, count) if count else ""
        words, chars, box = [], [], None

        def flush():
            if chars:
                x0, t, x1, b = box
                words.append(Word("".join(chars), x0, x1, t, b))

        for i in range(min(count, len(text))):
            ch = text[i]
            if ch.isspace():
                flush()
                chars, box = [], None
                continue
            l, b, r, t = textpage.get_charbox(i, loose=True)
            # PDF space (origin bottom-left) -> page space (origin top-left)
            cell = (l - left, top - t, r - left, top - b)
            if box is not None and _breaks_word(box, cell):
                # No space in the text stream, but the next glyph is elsewhere on the
                # page (another line or column): PDFium's reading order joined them.
                flush()
                chars, box = [], None
            if ch == "￾":
                # PDFium's marker for a hyphen at the end of a line inside a cell
                # ("CLOSURE-"). Keep the hyphen and end the word: the next glyph is
                # on another line, often in another column.
                chars.append("-")
                box = cell if box is None else _union(box, cell)
                flush()
                chars, box = [], None
                continue
            box = cell if box is None else _union(box, cell)
            chars.append(ch)
        flush()
        return tuple(words)

    def close(self):
        self._doc.close()


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _breaks_word(box, cell):
    """True when `cell` cannot continue the word in `box`: it starts left of the
    word's end (a new line or column) or sits on a different baseline."""
    x0, top, x1, bottom = box
    height = max(bottom - top, 1.0)
    moved_back = cell[0] < x1 - height * 0.5
    other_line = abs(cell[3] - bottom) > height * 0.5
    return moved_back or other_line
