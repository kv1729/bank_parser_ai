"""The owner's approvals and corrections as training examples (Stage 10.7, D-027).
Anonymised layout copies and synthetic data only."""
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import pytest

from bank_parser.api.main import create_app
from bank_parser.config import Settings
from bank_parser.core.schema import statement_to_dict
from bank_parser.core.validation import report_to_dict, validate_statement
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.engine import extract_with_template
from bank_parser.learning import llm_view
from bank_parser.learning.agent import LearningContext
from bank_parser.learning.examples import (ExamplesProposer, few_shot, nearest, normalise_heading, record_golden,
                                           record_review, table_example, vocabulary, vocabulary_roles)
from bank_parser.pdf.json_layout import load_layout
from bank_parser.self_review import apply_history, build_self_review
from bank_parser.storage import Database
from bank_parser.templates import TemplateRegistry, template_from_dict
from tests.helpers import COMMITTED_TEMPLATES, SBI_PDF

LAYOUTS = Path(__file__).parent / "fixtures" / "layouts"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from llm_choice import renamed_headings  # noqa: E402  (other banks' heading words, same positions)


def _copy(name):
    _, doc = load_layout(LAYOUTS / name / "layout.json")
    t = template_from_dict(json.loads((LAYOUTS / name / "template.json").read_text(encoding="utf-8")))
    return doc, t


def _roles(t):
    return [c.role for c in sorted(t.table.columns, key=lambda c: c.x0)]


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_document("doc-a", "a.pdf", 1)
    db.upsert_document("doc-b", "b.pdf", 1)
    return db


@pytest.mark.parametrize("name", ["boi-23f4f42a", "icici-ec27b806"])
def test_table_example_has_the_approved_roles_and_no_values(name):
    doc, t = _copy(name)
    ex = table_example(doc, t)
    assert [r for r in ex["roles"] if r != "ignore"] == [r for r in _roles(t) if r != "ignore"]
    view = llm_view.find_first_table(doc)
    text = json.dumps(ex)
    assert not [v for vals in view.samples for v in vals if len(v) > 2 and v in text]


def test_normalise_heading_drops_values_and_repeats():
    assert normalise_heading("Posting  Date Posting Date") == "posting date"
    assert normalise_heading("Withdrawal (Dr.)") == "withdrawal dr"
    assert normalise_heading("01/04/2024") == "" and normalise_heading("a b c d e f") == ""


def test_vocabulary_keeps_only_unambiguous_headings():
    ex = [{"headings": ["amt", "date"], "roles": ["debit", "txn_date"]},
          {"headings": ["amt", "particulars"], "roles": ["credit", "description"]}]
    assert vocabulary(ex) == {"date": "txn_date", "particulars": "description"}


def test_examples_label_a_table_whose_wording_was_approved_before(tmp_path):
    icici, ti = _copy("icici-ec27b806")
    boi, tb = _copy("boi-23f4f42a")
    approved_doc, approved_t = renamed_headings(icici, ti)          # other wording, approved once
    new_doc, new_t = renamed_headings(boi, tb)                      # the same wording, a new layout
    view = llm_view.find_first_table(new_doc)
    assert llm_view.word_roles(view) is None                         # familiar words alone cannot
    examples = [{"payload": table_example(approved_doc, approved_t)}]
    roles, mode = vocabulary_roles(view, vocabulary(examples))
    assert roles == _roles(new_t) and mode == "columns"
    # The proposer builds a candidate the agent can check, without a model call.
    ctx = LearningContext(new_doc, new_doc, detection_text(new_doc), "doc-x", TemplateRegistry([], tmp_path))
    (candidate,) = ExamplesProposer(examples).propose(ctx)
    assert candidate.provenance["proposer"] == "examples"
    got = statement_to_dict(extract_with_template(new_doc, candidate).statement)
    want = statement_to_dict(extract_with_template(new_doc, new_t).statement)
    keys = ("txn_date", "description", "debit", "credit", "balance")
    assert [[t[k] for k in keys] for t in got["transactions"]] == [[t[k] for k in keys] for t in want["transactions"]]


def test_examples_do_nothing_when_they_add_nothing():
    doc, t = _copy("boi-23f4f42a")
    view = llm_view.find_first_table(doc)
    examples = [{"payload": table_example(doc, t)}]
    assert vocabulary_roles(view, vocabulary(examples)) is None      # familiar words already name them
    assert vocabulary_roles(view, {}) is None
    assert not ExamplesProposer([]).available()


def test_few_shot_shows_headings_and_roles_only():
    doc, t = _copy("icici-ec27b806")
    ex = {"payload": table_example(doc, t)}
    view = llm_view.find_first_table(doc)
    text = few_shot(nearest([ex], view))
    assert text.startswith("Example 1:") and "-> txn_date" in text
    prompt = llm_view.build_prompt(view, text)
    assert "Tables the owner approved" in prompt and prompt.count("```") == 4
    assert llm_view.build_prompt(view) == llm_view.build_prompt(view, "")


def test_review_examples_are_appended_never_edited(tmp_path):
    db = _db(tmp_path)
    sr = {"unsure": [{"id": "row:3:money", "field": "txn.3", "level": "low"}]}
    n = record_review(db, "doc-a", 1, "boi/layout-x/v1", ["header.branch", "txn.3"], [],
                      {"row:3:money": {"verdict": "agree", "note": "private note"}}, sr)
    assert n == 2
    assert record_review(db, "doc-a", 1, "boi/layout-x/v1", ["header.branch", "txn.3"], [],
                         {"row:3:money": {"verdict": "agree", "note": ""}}, sr) == 0      # same again: no-op
    first = db.examples()
    assert record_review(db, "doc-a", 1, "boi/layout-x/v1", ["header.branch"], ["header.ifsc"], {}, sr) == 1
    assert db.examples()[:2] == first                                                   # older rows unchanged
    corr = db.examples("field_correction")[0]["payload"]
    assert corr == {"extraction_id": 1, "wrong": ["header.branch"], "missing": [], "rows_marked": 1}
    verdict = db.examples("verdict")[0]["payload"]
    assert verdict["item"] == "row:*:money" and "note" not in verdict
    assert db.example_counts() == {"field_correction": 2, "verdict": 1}


def test_recording_never_raises():
    class Broken:
        def add_example(self, *a):
            raise RuntimeError("disk full")
    assert record_review(Broken(), "d", 1, None, ["header.branch"], [], {}, {}) == 0
    assert record_golden(Broken(), "d", {"template_id": None, "version": 1}) == 0


def _sbi_review():
    t = next(t for t in TemplateRegistry([COMMITTED_TEMPLATES]).all() if t.template_id.startswith("sbi"))
    from bank_parser.pdf import open_pdf
    with open_pdf(SBI_PDF) as doc:
        res = extract_with_template(doc, t)
    st = statement_to_dict(res.statement)
    return t, lambda: build_self_review(st, report_to_dict(validate_statement(res.statement)), res.field_sources, t)


def test_corrections_cap_the_rating_on_the_same_layout_until_it_is_approved_again(tmp_path):
    db = _db(tmp_path)
    t, review = _sbi_review()
    assert review()["fields"]["header.ifsc"]["level"] == "high"
    record_review(db, "doc-a", 1, t.template_id, ["header.ifsc"], [], {}, {})
    sr = apply_history(review(), db.example_history(t.template_id))
    assert sr["fields"]["header.ifsc"]["level"] == "medium" and "corrected it" in sr["fields"]["header.ifsc"]["reason"]
    assert any(u["id"] == "history:header.ifsc" for u in sr["unsure"])
    # Another layout is not affected; a later version of the same layout is.
    assert db.example_history("hdfc/layout-other/v1") == []
    later = t.template_id.rsplit("/", 1)[0] + "/v99"
    assert apply_history(review(), db.example_history(later))["fields"]["header.ifsc"]["level"] == "medium"
    # Its own review does not change its own self-review.
    assert apply_history(review(), db.example_history(t.template_id), 1)["fields"]["header.ifsc"]["level"] == "high"
    # Approved again: the cap is lifted.
    record_golden(db, "doc-b", {"template_id": t.template_id, "version": 1})
    assert apply_history(review(), db.example_history(t.template_id))["fields"]["header.ifsc"]["level"] == "high"


def test_feedback_is_ordered_by_when_it_happened_not_when_it_was_recorded(tmp_path):
    """A backfill records an old correction after the approval that followed it: it must not count."""
    db = _db(tmp_path)
    record_golden(db, "doc-b", {"template_id": "boi/layout-x/v3", "version": 1, "approved_at": "2026-10-14T10:18:00+00:00"})
    record_review(db, "doc-a", 1, "boi/layout-x/v1", ["header.branch"], [], {}, {}, "2026-10-14T09:11:59+00:00")
    assert db.example_history("boi/layout-x/v3") == []
    record_review(db, "doc-a", 2, "boi/layout-x/v3", ["header.branch"], [], {}, {}, "2026-10-15T08:00:00+00:00")
    assert [h["extraction_id"] for h in db.example_history("boi/layout-x/v3")] == [2]


def test_a_worry_called_not_useful_twice_is_muted_and_listed_last(tmp_path):
    db = _db(tmp_path)
    t, review = _sbi_review()
    first = review()["unsure"][0]
    for ex_id in (1, 2):
        record_review(db, "doc-a", ex_id, t.template_id, [], [], {first["id"]: {"verdict": "disagree"}},
                      {"unsure": [first]})
    sr = apply_history(review(), db.example_history(t.template_id))
    muted = next(u for u in sr["unsure"] if u["id"] == first["id"])
    assert muted["muted"] and muted["owner_votes"] == {"agree": 0, "disagree": 2}
    assert sr["unsure"][-1]["id"] == first["id"]
    assert sr["history"] == {"corrections": 0, "verdicts": 2}


def test_app_records_review_examples_and_reports_counts(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        with open(SBI_PDF, "rb") as f:
            doc_id = client.post("/api/documents", files={"file": ("s.pdf", f, "application/pdf")}).json()["document_id"]
        for _ in range(100):
            if client.get(f"/api/documents/{doc_id}").json()["document"]["status"] not in ("RECEIVED", "PROCESSING"):
                break
            time.sleep(0.1)
        got = client.get(f"/api/documents/{doc_id}/review").json()
        item = got["self_review"]["unsure"][0]["id"]
        body = {"extraction_id": got["extraction_id"], "flags": ["header.branch"], "missing": [], "remarks": "",
                "agent_feedback": {item: {"verdict": "agree", "note": ""}}}
        assert client.put(f"/api/documents/{doc_id}/review", json=body).status_code == 200
        summary = client.get("/api/training-examples/summary").json()
        assert summary == {"total": 2, "by_kind": {"field_correction": 1, "verdict": 1}}
