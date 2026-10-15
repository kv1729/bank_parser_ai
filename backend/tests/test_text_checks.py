"""Checks for text, which arithmetic cannot verify (Stage 10.5, D-025)."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

import bank_parser.extraction.table as table
from bank_parser.core.schema import statement_from_dict, statement_to_dict
from bank_parser.core.text_checks import text_check, with_text_check
from bank_parser.core.validation import report_to_dict, validate_statement
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.engine import extract_with_template
from bank_parser.extraction.select import extract_known
from bank_parser.pdf import open_pdf
from bank_parser.pdf.json_layout import load_layout
from bank_parser.self_review import build_self_review
from bank_parser.templates import TemplateRegistry, template_from_dict
from tests.helpers import COMMITTED_TEMPLATES, HDFC_PDF, SBI_PDF

LAYOUTS = Path(__file__).parent / "fixtures" / "layouts"


def _copy(name):
    _, doc = load_layout(LAYOUTS / name / "layout.json")
    return doc, template_from_dict(json.loads((LAYOUTS / name / "template.json").read_text(encoding="utf-8")))


def _codes(check):
    return [i.code for i in check.issues]


@pytest.mark.parametrize("name", ["boi-23f4f42a", "icici-ec27b806", "sbi-d2e542a8"])
def test_correct_real_layouts_give_no_text_warnings(name):
    doc, t = _copy(name)
    res = extract_with_template(doc, t)
    check = text_check(res.statement, res.text_trace)
    assert check.status.value == "PASS" and check.issues == ()


@pytest.mark.parametrize("pdf", [SBI_PDF, HDFC_PDF], ids=["sbi", "hdfc"])
def test_correct_synthetic_statements_give_no_text_warnings(pdf):
    with open_pdf(pdf) as doc:
        _, res, _ = extract_known(doc, detection_text(doc), TemplateRegistry([COMMITTED_TEMPLATES]).all())
    assert text_check(res.statement, res.text_trace).issues == ()


def test_moved_first_lines_are_caught(monkeypatch):
    """Without the lead-line rule (D-018), each row's first line moves up to the previous row."""
    doc, t = _copy("sbi-d2e542a8")
    good = extract_with_template(doc, t).statement
    monkeypatch.setattr(table, "_split_lead_lines", lambda pending, anchor, line: (pending, []))
    res = extract_with_template(doc, t)
    check = text_check(res.statement, res.text_trace)
    changed = {i for i, (a, b) in enumerate(zip(good.transactions, res.statement.transactions))
               if a.description != b.description}
    flagged = {i.row_index for i in check.issues if i.code == "description_start_at_end"}
    assert changed and flagged
    assert all(r in flagged or r - 1 in flagged or r + 1 in flagged for r in changed)     # every change is near a flag
    assert flagged <= changed | {r - 1 for r in changed} | {r + 1 for r in changed}       # no flag on untouched rows
    assert "text_not_captured" in _codes(check)        # the very first row's line had nowhere to go


@pytest.mark.parametrize("name", ["icici-ec27b806", "boi-23f4f42a"])
def test_lost_text_is_caught_when_a_column_is_too_narrow(name):
    doc, t = _copy(name)
    cols = sorted(t.table.columns, key=lambda c: c.x0)
    k = [c.role for c in cols].index("description")
    cut = cols[k].x0 + (cols[k].x1 - cols[k].x0) * 0.45
    narrow = tuple(replace(c, x1=cut) if i == k else replace(c, x0=cut) if i == k + 1 else c for i, c in enumerate(cols))
    res = extract_with_template(doc, replace(t, table=replace(t.table, columns=narrow)))
    assert "text_not_captured" in _codes(text_check(res.statement, res.text_trace))


def test_empty_description_and_label_in_value():
    doc, t = _copy("icici-ec27b806")
    st = statement_to_dict(extract_with_template(doc, t).statement)
    st["transactions"][4]["description"] = ""
    st["header"]["account_holder_address"] = (st["header"]["account_holder_address"] or "") + " Branch Code"
    check = text_check(statement_from_dict(st))
    assert ("empty_description", 4) in [(i.code, i.row_index) for i in check.issues]
    label = next(i for i in check.issues if i.code == "label_in_value")
    assert "account_holder_address" in label.message and "branch" in label.message


def test_messages_and_self_review_contain_no_statement_text(monkeypatch):
    doc, t = _copy("sbi-d2e542a8")
    monkeypatch.setattr(table, "_split_lead_lines", lambda pending, anchor, line: (pending, []))
    res = extract_with_template(doc, t)
    st = statement_to_dict(res.statement)
    check = text_check(res.statement, res.text_trace)
    report = with_text_check(report_to_dict(validate_statement(res.statement)), check)
    sr = build_self_review(st, report, res.field_sources, t)
    blob = json.dumps(report) + json.dumps(sr)
    texts = [x["description"] for x in st["transactions"] if x["description"]]
    assert texts and not [d for d in texts if d in blob]
    assert any(u["id"].startswith("text:description_start_at_end") for u in sr["unsure"])


def test_text_check_turns_pass_into_warn_but_not_fail():
    doc, t = _copy("icici-ec27b806")
    st = statement_to_dict(extract_with_template(doc, t).statement)
    st["transactions"][2]["description"] = ""
    statement = statement_from_dict(st)
    report = with_text_check({"status": "PASS", "verified": True, "checks": []}, text_check(statement))
    assert report["status"] == "WARN" and report["verified"] is True
    assert report["checks"][-1]["check"] == "text"
