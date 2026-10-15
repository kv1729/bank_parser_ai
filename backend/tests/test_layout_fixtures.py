"""
Anonymised copies of the golden statements' layouts (Stage 9, D-022).

Each folder in fixtures/layouts/ holds the exact word positions of a real statement
with every value replaced (scripts/anonymise_layout.py), its template, and the output
the engine produced on the copy when it was made. CI re-extracts every copy and
requires identical output, so engine changes are checked against real layouts
without any real data.
"""
import json
from pathlib import Path

import pytest

from bank_parser.core.schema import statement_to_dict
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.select import extract_known
from bank_parser.golden import diff, snapshot
from bank_parser.pdf.json_layout import load_layout
from bank_parser.templates import TemplateRegistry, template_from_dict
from tests.helpers import COMMITTED_TEMPLATES

LAYOUTS = Path(__file__).parent / "fixtures" / "layouts"
FIXTURES = sorted(p for p in LAYOUTS.iterdir() if (p / "layout.json").exists()) if LAYOUTS.is_dir() else []


def _load(folder):
    _, doc = load_layout(folder / "layout.json")
    template = template_from_dict(json.loads((folder / "template.json").read_text(encoding="utf-8")))
    expected = json.loads((folder / "output.json").read_text(encoding="utf-8"))
    return doc, template, expected


@pytest.mark.parametrize("folder", FIXTURES, ids=[p.name for p in FIXTURES])
def test_layout_copy_extracts_exactly_as_recorded(folder):
    doc, template, expected = _load(folder)
    # The layout's own template among all committed ones: no committed template may take it over.
    templates = TemplateRegistry([COMMITTED_TEMPLATES]).all() + [template]
    best = extract_known(doc, detection_text(doc), templates)
    assert best is not None, "the layout's template no longer extracts its copy"
    used, result, report = best
    assert used.template_id == template.template_id
    changes = diff(expected["statement"], snapshot(statement_to_dict(result.statement)))
    assert changes == [], [c.path for c in changes][:20]
    assert report.status.value == expected["validation_status"]


def test_all_golden_layouts_have_copies():
    assert {p.name for p in FIXTURES} >= {"sbi-d2e542a8", "boi-23f4f42a", "icici-ec27b806"}
