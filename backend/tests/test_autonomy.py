"""Confidence & autonomy (Stage 11, D-029). Synthetic database histories only."""
import time

from fastapi.testclient import TestClient

from bank_parser.api.main import create_app
from bank_parser.autonomy import layout_earned, track_record
from bank_parser.config import Settings
from bank_parser.storage import Database
from tests.helpers import COMMITTED_TEMPLATES

REPORT = {"status": "PASS", "verified": True, "checks": []}
LAYOUT = "boi/layout-x"


class History:
    """Builds a document history: one extraction per statement, then the owner's review."""

    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "h.db")
        self.n = 0

    def statement(self, outcome, template=f"{LAYOUT}/v1", corrected_before=False):
        self.n += 1
        doc = f"doc-{self.n:03d}"
        self.db.upsert_document(doc, f"{doc}.pdf", 1)
        if corrected_before:
            ex = self.db.add_extraction(doc, "known_template", "template", template, REPORT, {}, [], {})
            self.db.save_review(doc, ex, ["header.branch"], "")
        ex = self.db.add_extraction(doc, "known_template", "template", template, REPORT, {}, [], {})
        if outcome == "matched":
            self.db.save_review(doc, ex, [], "")
        elif outcome == "corrected":
            self.db.save_review(doc, ex, ["txn.3"], "", ["header.ifsc"])
        return doc


def _record(h, threshold=3):
    (r,) = [r for r in track_record(h.db, threshold) if r["layout"] == LAYOUT]
    return r


def test_the_run_counts_matches_since_the_last_correction(tmp_path):
    h = History(tmp_path)
    for o in ("matched", "corrected", "matched", "unreviewed", "matched"):
        h.statement(o)
    r = _record(h)
    assert (r["statements"], r["matched"], r["corrected"], r["unreviewed"]) == (5, 3, 1, 1)
    assert r["run"] == 2 and not r["earned"]                     # the unreviewed one neither breaks nor counts
    h.statement("matched")
    assert _record(h)["earned"] and layout_earned(h.db, 3, f"{LAYOUT}/v7")


def test_a_statement_corrected_earlier_never_counts_as_matched(tmp_path):
    h = History(tmp_path)
    for _ in range(3):
        h.statement("matched")
    h.statement("matched", corrected_before=True)                # fixed by code later: still a correction
    r = _record(h)
    assert r["corrected"] == 1 and r["run"] == 0


def test_versions_of_a_layout_share_one_record_and_other_layouts_are_separate(tmp_path):
    h = History(tmp_path)
    h.statement("matched", f"{LAYOUT}/v1")
    h.statement("matched", f"{LAYOUT}/v2")
    h.statement("corrected", "sbi/layout-y/v1")
    assert _record(h)["run"] == 2
    assert not layout_earned(h.db, 3, "sbi/layout-y/v1") and not layout_earned(h.db, 3, None)


def test_an_owner_golden_counts_as_matched(tmp_path):
    h = History(tmp_path)
    doc = h.statement("unreviewed")
    ex = h.db.latest_extraction(doc)["id"]
    h.db.add_golden(doc, ex, {}, "known_template", f"{LAYOUT}/v1", "pypdfium2", None, "PASS", [], "none")
    assert _record(h)["matched"] == 1


def test_autonomy_api_reports_counts_only(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES,
                                 autonomy_threshold=4)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        got = client.get("/api/autonomy").json()
    assert got == {"threshold": 4, "enabled": False, "layouts": []}


# --- 11.2 confidence per statement ------------------------------------------------------------

def _sbi():
    from bank_parser.core.schema import statement_to_dict
    from bank_parser.core.validation import report_to_dict, validate_statement
    from bank_parser.extraction.engine import extract_with_template
    from bank_parser.pdf import open_pdf
    from bank_parser.templates import TemplateRegistry
    from tests.helpers import SBI_PDF
    t = next(t for t in TemplateRegistry([COMMITTED_TEMPLATES]).all() if t.template_id.startswith("sbi"))
    with open_pdf(SBI_PDF) as doc:
        res = extract_with_template(doc, t)
    return t, res, statement_to_dict(res.statement), report_to_dict(validate_statement(res.statement))


def test_a_verified_statement_is_high_and_says_what_its_layout_record_covers():
    from bank_parser.self_review import build_self_review
    t, res, st, report = _sbi()
    c = build_self_review(st, report, res.field_sources, t)["confidence"]
    assert c["level"] == "high" and c["reasons"] == []
    assert all(k.startswith(("field:", "learning:")) for k in c["covered_by_track_record"])


def test_a_broken_balance_is_low_and_says_why():
    from bank_parser.core.schema import statement_from_dict
    from bank_parser.core.validation import report_to_dict, validate_statement
    from bank_parser.self_review import build_self_review
    t, res, st, _ = _sbi()
    st["transactions"][3]["balance"] = "1.00"
    report = report_to_dict(validate_statement(statement_from_dict(st)))
    c = build_self_review(st, report, res.field_sources, t)["confidence"]
    assert c["level"] == "low" and "validation_failed" in c["reasons"] and "low:transactions.balance" in c["reasons"]


def test_an_earlier_correction_on_the_layout_makes_it_medium(tmp_path):
    from bank_parser.learning.examples import record_review
    from bank_parser.self_review import apply_history, build_self_review
    t, res, st, report = _sbi()
    db = Database(tmp_path / "c.db")
    db.upsert_document("d", "d.pdf", 1)
    record_review(db, "d", 1, t.template_id, ["header.branch"], [], {}, {})
    sr = apply_history(build_self_review(st, report, res.field_sources, t), db.example_history(t.template_id))
    assert sr["confidence"]["level"] == "medium" and "check:history:header.branch" in sr["confidence"]["reasons"]


def test_without_a_template_it_is_never_high():
    from bank_parser.self_review import build_self_review
    _, res, st, report = _sbi()
    c = build_self_review(st, report, res.field_sources, None)["confidence"]
    assert c["level"] == "medium" and "no_template" in c["reasons"]


def test_older_self_reviews_get_a_confidence():
    from bank_parser.self_review import build_self_review, ensure_confidence
    t, res, st, report = _sbi()
    sr = build_self_review(st, report, res.field_sources, t)
    sr.pop("confidence"), sr.pop("basis")
    assert ensure_confidence(sr, st, report, t)["confidence"]["level"] == "high"
