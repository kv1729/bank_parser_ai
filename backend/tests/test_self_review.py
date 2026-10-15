"""The system's self-review of each extraction (Stage 10.4, D-024)."""
from dataclasses import replace
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

from bank_parser.api.main import create_app
from bank_parser.config import Settings
from bank_parser.core.schema import statement_from_dict, statement_to_dict
from bank_parser.core.validation import report_to_dict, validate_statement
from bank_parser.extraction.engine import extract_with_template
from bank_parser.pdf import open_pdf
from bank_parser.pdf.json_layout import load_layout
from bank_parser.self_review import build_self_review, calibration
from bank_parser.templates import TemplateRegistry, template_from_dict
from tests.helpers import COMMITTED_TEMPLATES, HDFC_LAYOUT, SBI_LAYOUT, SBI_PDF, HDFC_PDF

LAYOUTS = Path(__file__).parent / "fixtures" / "layouts"


def _template(prefix):
    return next(t for t in TemplateRegistry([COMMITTED_TEMPLATES]).all() if t.template_id.startswith(prefix))


def _review(pdf, prefix, template=None):
    t = template or _template(prefix)
    with open_pdf(pdf) as doc:
        res = extract_with_template(doc, t)
    st = statement_to_dict(res.statement)
    return st, build_self_review(st, report_to_dict(validate_statement(res.statement)), res.field_sources, t)


def test_verified_statement_rates_money_high_and_text_medium():
    st, sr = _review(SBI_PDF, SBI_LAYOUT)
    assert sr["fields"]["transactions.balance"]["level"] == "high"
    assert sr["fields"]["transactions.debit"]["level"] == "high"
    assert sr["fields"]["transactions.description"]["level"] == "medium"
    assert any(u["id"] == "text:descriptions" for u in sr["unsure"])
    assert sr["summary"].startswith(f"Money verified on")
    assert any(c.startswith("Balance chain") for c in sr["checked"])


def test_self_review_contains_no_statement_values():
    st, sr = _review(SBI_PDF, SBI_LAYOUT)
    text = json.dumps(sr)
    values = [t["description"] for t in st["transactions"] if t["description"]]
    values += [t[k] for t in st["transactions"] for k in ("debit", "credit", "balance") if t[k]]
    values += [v for k, v in st["header"].items() if isinstance(v, str) and len(v) > 3 and k != "currency"]
    assert values and not [v for v in values if v in text]


def test_no_opening_balance_flags_the_first_row():
    _, sr = _review(HDFC_PDF, HDFC_LAYOUT)
    assert any(u["id"] == "row:0:no_opening_balance" and u["field"] == "txn.0" for u in sr["unsure"])


def test_a_row_that_does_not_add_up_is_rated_low():
    t = _template(SBI_LAYOUT)
    with open_pdf(SBI_PDF) as doc:
        st = statement_to_dict(extract_with_template(doc, t).statement)
    st["transactions"][3]["balance"] = "1.00"
    report = report_to_dict(validate_statement(statement_from_dict(st)))
    sr = build_self_review(st, report, {}, t)
    assert sr["fields"]["transactions.balance"]["level"] == "low"
    row = next(u for u in sr["unsure"] if u["id"] == "row:3:money")
    assert row["level"] == "low" and "does not follow" in row["text"]


def test_field_from_a_position_on_the_page_is_listed_with_its_reason():
    _, doc = load_layout(LAYOUTS / "icici-ec27b806" / "layout.json")
    t = template_from_dict(json.loads((LAYOUTS / "icici-ec27b806" / "template.json").read_text(encoding="utf-8")))
    res = extract_with_template(doc, t)
    sr = build_self_review(statement_to_dict(res.statement), report_to_dict(validate_statement(res.statement)),
                           res.field_sources, t)
    item = next(u for u in sr["unsure"] if u["id"] == "field:header.account_holder_name")
    assert "position on the page" in item["text"]
    assert sr["fields"]["header.account_holder_name"]["level"] == "medium"


def test_template_learned_by_the_local_model_says_so():
    t = _template(SBI_LAYOUT)
    t = replace(t, provenance={"created_by": "template_agent", "proposer": "llm", "role_source": "llm",
                               "model": "qwen3.5:9b", "swapped": True})
    _, sr = _review(SBI_PDF, SBI_LAYOUT, t)
    assert sr["learning"]["proposer"] == "the local AI model" and sr["learning"]["model"] == "qwen3.5:9b"
    ids = {u["id"] for u in sr["unsure"]}
    assert {"learning:llm_columns", "learning:swap"} <= ids


def test_calibration_counts_wrong_fields_rated_high():
    _, sr = _review(SBI_PDF, SBI_LAYOUT)
    assert sr["fields"]["header.ifsc"]["level"] == "high"
    cal = calibration(sr, {"flags": ["header.ifsc", "txn.2"], "missing": [], "agent_feedback": {}})
    assert cal["wrong_but_rated_high"] == ["header.ifsc"]
    assert cal["rows_marked_wrong_not_flagged"] == [2]


def test_owner_feedback_round_trip(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        with open(SBI_PDF, "rb") as f:
            doc_id = client.post("/api/documents", files={"file": ("s.pdf", f, "application/pdf")}).json()["document_id"]
        for _ in range(100):
            if client.get(f"/api/documents/{doc_id}").json()["document"]["status"] not in ("RECEIVED", "PROCESSING"):
                break
            time.sleep(0.1)
        got = client.get(f"/api/documents/{doc_id}/review").json()
        sr, ex_id = got["self_review"], got["extraction_id"]
        item = sr["unsure"][0]["id"]
        base = {"extraction_id": ex_id, "flags": [], "missing": [], "remarks": ""}

        r = client.put(f"/api/documents/{doc_id}/review",
                       json={**base, "agent_feedback": {item: {"verdict": "disagree", "note": "fine as is"}}})
        assert r.status_code == 200 and r.json()["agent_feedback"][item]["verdict"] == "disagree"
        # Saving without agent_feedback keeps the stored verdicts.
        assert client.put(f"/api/documents/{doc_id}/review", json=base).json()["agent_feedback"][item]["note"] == "fine as is"
        # Only items the self-review lists, and only agree/disagree.
        assert client.put(f"/api/documents/{doc_id}/review",
                          json={**base, "agent_feedback": {"made:up": {"verdict": "agree"}}}).status_code == 400
        assert client.put(f"/api/documents/{doc_id}/review",
                          json={**base, "agent_feedback": {item: {"verdict": "maybe"}}}).status_code == 400
        assert client.get(f"/api/documents/{doc_id}/review").json()["calibration"]["feedback"] == 1
