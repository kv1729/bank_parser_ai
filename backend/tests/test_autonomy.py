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


# --- 11.3 routing ----------------------------------------------------------------------------

def test_only_a_verified_confident_statement_of_an_earned_layout_skips_the_owner():
    from bank_parser.autonomy import route
    earned = {"layout": LAYOUT, "run": 10, "threshold": 10, "earned": True}
    learning = {**earned, "run": 4, "earned": False}
    high, medium = {"level": "high"}, {"level": "medium"}
    assert route(high, True, f"{LAYOUT}/v2", earned) == {"required": False, "reason": "earned_and_high",
                                                         "confidence": "high", "layout": LAYOUT, "run": 10,
                                                         "threshold": 10}
    assert route(high, True, f"{LAYOUT}/v2", learning)["reason"] == "layout_not_earned"
    assert route(medium, True, f"{LAYOUT}/v2", earned)["reason"] == "confidence_medium"
    assert route(high, False, f"{LAYOUT}/v2", earned)["reason"] == "not_verified"
    assert route(high, True, None, None)["reason"] == "no_layout"


def _upload(tmp_path, **overrides):
    from tests.helpers import SBI_PDF
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES, **overrides)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        with open(SBI_PDF, "rb") as f:
            doc_id = client.post("/api/documents", files={"file": ("s.pdf", f, "application/pdf")}).json()["document_id"]
        for _ in range(100):
            got = client.get(f"/api/documents/{doc_id}").json()
            if got["document"]["status"] not in ("RECEIVED", "PROCESSING"):
                break
            time.sleep(0.1)
        return got, client.get(f"/api/documents/{doc_id}/review").json()


def test_today_every_statement_still_goes_to_the_owner(tmp_path):
    got, review = _upload(tmp_path)
    r = got["extraction"]["routing"]
    assert got["document"]["status"] == "EXTRACTED"                       # statuses keep their meaning
    assert r["required"] and r["reason"] == "layout_not_earned" and r["run"] == 0 and r["threshold"] == 10
    assert review["routing"] == r


def test_an_earned_layout_with_a_confident_statement_is_not_sent_to_the_owner(tmp_path):
    got, _ = _upload(tmp_path, autonomy_threshold=0)
    assert got["extraction"]["routing"]["required"] is False
    assert got["extraction"]["routing"]["reason"] == "earned_and_high"


# --- 11.4 the agent approves golden snapshots ---------------------------------------------------

def _golden_after_upload(tmp_path, reprocess=False, **overrides):
    from tests.helpers import SBI_PDF
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES, **overrides)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        with open(SBI_PDF, "rb") as f:
            doc_id = client.post("/api/documents", files={"file": ("s.pdf", f, "application/pdf")}).json()["document_id"]

        def golden(wait_for=None):
            for _ in range(300):               # up to 30 s: the approval re-extracts the statement
                g = client.get(f"/api/documents/{doc_id}/golden").json()["golden"]
                status = client.get(f"/api/documents/{doc_id}").json()["document"]["status"]
                if status not in ("RECEIVED", "PROCESSING") and (wait_for is None or g is not None):
                    return g
                time.sleep(0.1)
            return g
        first = golden(wait_for=True if overrides.get("autonomy") else None)
        if not reprocess:
            return first
        client.post(f"/api/documents/{doc_id}/reprocess")
        time.sleep(0.5)
        return first, golden(), client.get(f"/api/documents/{doc_id}").json()["extraction"]["id"]


def test_the_agent_never_approves_while_autonomy_is_off(tmp_path):
    assert _golden_after_upload(tmp_path, autonomy_threshold=0) is None


def test_with_autonomy_on_an_earned_confident_statement_becomes_golden_approved_by_the_agent(tmp_path):
    g = _golden_after_upload(tmp_path, autonomy_threshold=0, autonomy=True)
    assert g is not None and g["approved_by"] == "agent" and g["version"] == 1


def test_the_agent_never_approves_a_second_version(tmp_path):
    first, again, _ = _golden_after_upload(tmp_path, reprocess=True, autonomy_threshold=0, autonomy=True)
    assert first["version"] == 1 and again["version"] == 1           # only the owner approves a new version


def test_the_agent_does_not_approve_what_the_owner_must_review(tmp_path):
    assert _golden_after_upload(tmp_path, autonomy=True) is None      # threshold 10: the layout has not earned it


def test_agent_approvals_do_not_count_in_the_track_record(tmp_path):
    h = History(tmp_path)
    doc = h.statement("unreviewed")
    ex = h.db.latest_extraction(doc)["id"]
    h.db.add_golden(doc, ex, {}, "known_template", f"{LAYOUT}/v1", "pypdfium2", None, "PASS", [], "none",
                    approved_by="agent")
    r = _record(h)
    assert r["matched"] == 0 and r["unreviewed"] == 1


# --- 11.5 layout drift ---------------------------------------------------------------------------

def _committed():
    from bank_parser.templates import TemplateRegistry
    return TemplateRegistry([COMMITTED_TEMPLATES]).all()


def test_a_known_layout_that_no_longer_reads_is_drift():
    from bank_parser.autonomy import detect_drift
    templates = _committed()
    sbi = next(t for t in templates if t.bank_code == "sbi")
    bank, layout, reason = detect_drift(" ".join(sbi.markers), templates)
    assert (bank, reason) == ("sbi", "template_failed") and layout == sbi.template_id.rsplit("/", 1)[0]


def test_a_new_layout_from_a_known_bank_is_drift_and_an_unknown_bank_is_not():
    from bank_parser.autonomy import detect_drift
    templates = _committed()
    assert detect_drift("Account statement", templates, ifsc="SBIN0001234") == ("sbi", None, "new_layout_for_known_bank")
    assert detect_drift("Account statement", templates, ifsc="ZZZZ0001234") is None


def test_drift_is_stored_and_listed_without_content(tmp_path):
    db = Database(tmp_path / "d.db")
    db.upsert_document("d1", "d1.pdf", 1)
    db.add_drift("d1", "sbi", "sbi/layout-x", "template_failed")
    (e,) = db.drift_events("d1")
    assert (e["bank_code"], e["layout"], e["reason"]) == ("sbi", "sbi/layout-x", "template_failed")
    assert db.drift_events("other") == []
