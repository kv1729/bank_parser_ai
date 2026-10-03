"""
Deterministic bank/layout detection: which registered templates fit a document.

A template matches when every one of its markers appears as a whole phrase in
the text of the first pages (or the last page). Markers are field labels, not
values, so detection never depends on personal data.
"""
from dataclasses import dataclass
import re

from bank_parser.extraction.lines import page_text

DETECTION_PAGES = (1, 2, -1)


@dataclass(frozen=True)
class Match:
    template: object
    matched: int


def marker_regex(marker):
    words = [re.escape(w) for w in marker.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.IGNORECASE)


def detection_text(doc):
    count = doc.page_count
    numbers = []
    for p in DETECTION_PAGES:
        n = p if p > 0 else count + 1 + p
        if 1 <= n <= count and n not in numbers:
            numbers.append(n)
    return "\n".join(page_text(doc.page(n)) for n in numbers)


def markers_match(markers, text):
    return bool(markers) and all(marker_regex(m).search(text) for m in markers)


def detect(text, templates):
    """Templates whose markers all match, most specific (most markers) and newest first."""
    matches = [Match(t, len(t.markers)) for t in templates if markers_match(t.markers, text)]
    return sorted(matches, key=lambda m: (-m.matched, m.template.bank_code, m.template.layout_id, -m.template.version))
