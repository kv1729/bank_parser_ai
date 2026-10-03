"""Group positioned words into text lines (deterministic, backend-neutral)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    words: tuple            # Word objects, left to right
    top: float
    bottom: float

    @property
    def text(self):
        return " ".join(w.text for w in self.words)


def group_lines(words, tolerance=3.0):
    """Words whose tops are within `tolerance` points share a line."""
    lines, current, current_top = [], [], None
    for w in sorted(words, key=lambda w: (round(w.top, 1), w.x0)):
        if current and abs(w.top - current_top) > tolerance:
            lines.append(current)
            current = []
        if not current:
            current_top = w.top
        current.append(w)
    if current:
        lines.append(current)
    out = []
    for ws in lines:
        ws = sorted(ws, key=lambda w: w.x0)
        out.append(Line(tuple(ws), min(w.top for w in ws), max(w.bottom for w in ws)))
    return out


def page_text(page, tolerance=3.0):
    return "\n".join(l.text for l in group_lines(page.words, tolerance))
