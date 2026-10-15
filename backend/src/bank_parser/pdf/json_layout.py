"""
Recorded page layouts: positioned words stored as JSON, served as a PdfDocument.

Used for the anonymised layout fixtures (Stage 9, D-022): the exact geometry of a
real statement with every personal value replaced, so CI can run the engine on
real layouts without any real data. Ruled rectangles are not recorded (the
known-template path does not use them).
"""
import json
from pathlib import Path

from bank_parser.pdf.backend import PageLayout, PdfDocument, Word

LAYOUT_FORMAT = 1


class JsonLayoutDocument(PdfDocument):
    backend = "json_layout"

    def __init__(self, pages):
        self._pages = {p.number: p for p in pages}

    @property
    def page_count(self):
        return len(self._pages)

    def page(self, number, with_rects=False):
        return self._pages[number]


def pages_to_dict(pages, precision=2):
    r = lambda v: round(float(v), precision)
    return [{"number": p.number, "width": r(p.width), "height": r(p.height),
             "words": [[w.text, r(w.x0), r(w.x1), r(w.top), r(w.bottom)] for w in p.words]} for p in pages]


def pages_from_dict(items):
    return [PageLayout(int(p["number"]), float(p["width"]), float(p["height"]),
                       tuple(Word(str(t), float(a), float(b), float(c), float(d)) for t, a, b, c, d in p["words"]))
            for p in items]


def load_layout(path):
    """(metadata dict, JsonLayoutDocument) from a layout fixture file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != LAYOUT_FORMAT:
        raise ValueError(f"unsupported layout format {data.get('format')!r}")
    meta = {k: v for k, v in data.items() if k != "pages"}
    return meta, JsonLayoutDocument(pages_from_dict(data["pages"]))
