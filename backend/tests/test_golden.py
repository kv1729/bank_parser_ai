"""Golden statements and the non-regression gate (Stage 9, D-022). Synthetic samples only."""
from dataclasses import replace
import shutil
import time

import pytest
from fastapi.testclient import TestClient

import bank_parser.extraction.select as select_mod
from bank_parser.api.main import create_app
from bank_parser.config import Settings
from bank_parser.golden import Change, diff, golden_gate, run_regression, shape, snapshot
from bank_parser.learning import LearningStatus, TemplateAgent
from bank_parser.learning.proposers import Proposer
from bank_parser.storage import Database
from bank_parser.templates import TemplateRegistry
from tests.helpers import COMMITTED_TEMPLATES, HDFC_PDF, SBI_LAYOUT, SBI_PDF, have_pymupdf


@pytest.fixture
def client(tmp_path):
    private, synthetic = tmp_path / "private", tmp_path / "synthetic"
    private.mkdir()
    synthetic.mkdir()
    shutil.copy(SBI_PDF, synthetic / SBI_PDF.name)
    shutil.copy(HDFC_PDF, synthetic / HDFC_PDF.name)
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES,
                                 private_samples_dir=private, synthetic_samples_dir=synthetic)
    with TestClient(create_app(settings, learning_mode="thread")) as c:
        c.settings, c.private_dir = settings, private
        yield c


def _import(client, name):
    src = next(s for s in client.get("/api/review/sources").json() if s["name"] == name)
    doc_id = client.post(f"/api/review/sources/{src['source_id']}/import").json()["document_id"]
    for _ in range(150):
        body = client.get(f"/api/documents/{doc_id}").json()
        if body["document"]["status"] not in ("RECEIVED", "PROCESSING") and body["extraction"]:
            return doc_id, body["extraction"]["id"]
        time.sleep(0.1)
    raise AssertionError("document did not finish processing")


def _review(client, doc_id, ex_id, flags=(), missing=()):
    r = client.put(f"/api/documents/{doc_id}/review",
                   json={"extraction_id": ex_id, "flags": list(flags), "missing": list(missing), "remarks": ""})
    assert r.status_code == 200


def _approve(client, doc_id, ex_id, ack=False):
    return client.post(f"/api/documents/{doc_id}/golden", json={"extraction_id": ex_id, "acknowledge_warnings": ack})


def _approved(client, name, ack=False):
    doc_id, ex_id = _import(client, name)
    _review(client, doc_id, ex_id)
    r = _approve(client, doc_id, ex_id, ack)
    assert r.status_code == 200, r.json()
    return doc_id, ex_id


# --- diff -------------------------------------------------------------------------------------

def test_diff_names_each_changed_field():
    old = {"schema_version": 2, "header": {"branch": "A", "ifsc": "X"}, "summary": None,
           "transactions": [{"debit": "1.00", "credit": None}, {"debit": None, "credit": "2.00"}]}
    new = {"schema_version": 2, "header": {"branch": "B", "ifsc": "X"}, "summary": None,
           "transactions": [{"debit": "1.50", "credit": None}]}
    assert diff(old, new) == [Change("header.branch", "A", "B"), Change("transactions", "2 rows", "1 rows"),
                              Change("transactions[0].debit", "1.00", "1.50")]
    assert diff(old, old) == []
    assert snapshot({**old, "provenance": {"extracted_at": "now"}}) == old
    assert shape("Ravi 12/3") == "Aaaa 99/9"


# --- approval rules ---------------------------------------------------------------------------

def test_approve_needs_a_clean_saved_review(client):
    doc_id, ex_id = _import(client, SBI_PDF.name)
    assert _approve(client, doc_id, ex_id).json()["detail"]["code"] == "no_review"
    _review(client, doc_id, ex_id, flags=["header.branch"])
    assert _approve(client, doc_id, ex_id).json()["detail"]["code"] == "review_marks"
    _review(client, doc_id, ex_id, missing=["header.bank_address"])
    assert _approve(client, doc_id, ex_id).json()["detail"]["code"] == "review_marks"
    _review(client, doc_id, ex_id)
    r = _approve(client, doc_id, ex_id)
    assert r.status_code == 200 and r.json()["golden"]["version"] == 1
    g = client.get(f"/api/documents/{doc_id}/golden").json()
    assert g["golden"]["extraction_id"] == ex_id and g["differs"] == []
    src = next(s for s in client.get("/api/review/sources").json() if s["document_id"] == doc_id)
    assert src["golden_version"] == 1


def test_warnings_must_be_acknowledged(client):
    doc_id, ex_id = _import(client, HDFC_PDF.name)          # no opening balance printed -> WARN
    _review(client, doc_id, ex_id)
    r = _approve(client, doc_id, ex_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "warnings"
    assert "no_opening_balance" in r.json()["detail"]["warnings"]
    r = _approve(client, doc_id, ex_id, ack=True)
    assert r.status_code == 200 and "no_opening_balance" in r.json()["golden"]["warnings"]


def test_only_latest_extraction_and_reapproval_adds_a_version(client):
    doc_id, first = _approved(client, SBI_PDF.name)
    assert client.post(f"/api/documents/{doc_id}/reprocess").status_code == 200
    for _ in range(150):
        latest = client.get(f"/api/documents/{doc_id}").json()["extraction"]["id"]
        if latest != first:
            break
        time.sleep(0.1)
    assert _approve(client, doc_id, first).json()["detail"]["code"] == "not_latest"
    _review(client, doc_id, latest)
    r = _approve(client, doc_id, latest)
    assert r.json()["golden"]["version"] == 2
    db = Database(client.settings.db_path)
    with db.connect() as c:
        assert [row[0] for row in c.execute("SELECT version FROM golden_snapshots WHERE document_id = ?", (doc_id,))] == [1, 2]


def test_stale_review_is_refused(client):
    """The stored output no longer matches what the current code extracts -> re-extract and review again."""
    doc_id, ex_id = _import(client, SBI_PDF.name)
    _review(client, doc_id, ex_id)
    db = Database(client.settings.db_path)
    with db.connect() as c:
        c.execute("UPDATE extractions SET statement_json = replace(statement_json, '\"bank_name\": \"', '\"bank_name\": \"X') "
                  "WHERE id = ?", (ex_id,))
    r = _approve(client, doc_id, ex_id)
    assert r.json()["detail"]["code"] == "stale" and "header.bank_name" in r.json()["detail"]["fields"]


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build an encrypted test PDF")
def test_encrypted_pdf_needs_a_password_file_line(client):
    import pymupdf
    doc = pymupdf.open(SBI_PDF)
    doc.save(client.private_dir / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw9", owner_pw="o")
    doc.close()
    src = next(s for s in client.get("/api/review/sources").json() if s["name"] == "locked.pdf")
    doc_id = client.post(f"/api/review/sources/{src['source_id']}/import").json()["document_id"]
    assert client.post(f"/api/documents/{doc_id}/unlock", data={"password": "pw9"}).status_code == 200
    client.post(f"/api/documents/{doc_id}/reprocess", data={"password": "pw9"})
    ex = None
    for _ in range(150):
        ex = client.get(f"/api/documents/{doc_id}").json()["extraction"]
        if ex:
            break
        time.sleep(0.1)
    if ex is None:
        pytest.skip("encrypted document was not processed without a folder password")
    _review(client, doc_id, ex["id"])
    assert _approve(client, doc_id, ex["id"]).json()["detail"]["code"] == "no_password"
    (client.private_dir / ".pdf_password").write_text("pw9\n", encoding="utf-8")
    assert _approve(client, doc_id, ex["id"]).status_code == 200


# --- the regression run (exit criterion 1) -------------------------------------------------------

def test_regression_passes_then_catches_one_changed_field(client, monkeypatch):
    doc_id, _ = _approved(client, SBI_PDF.name)
    hdfc_id, _ = _approved(client, HDFC_PDF.name, ack=True)
    db, s = Database(client.settings.db_path), client.settings
    templates = TemplateRegistry([COMMITTED_TEMPLATES], s.learned_templates_dir).all()
    report = run_regression(db, s, templates)
    assert report.ok and {r.status for r in report.results} == {"PASS"} and len(report.results) == 2

    real, changed = select_mod.extract_with_template, []

    def engine_with_a_bug(doc, template, *a, **k):        # one SBI description (row 5) loses its last word
        result = real(doc, template, *a, **k)
        if template.bank_code != "sbi":
            return result
        txns = list(result.statement.transactions)
        txns[5] = replace(txns[5], description=txns[5].description.rsplit(" ", 1)[0] + " X")
        changed.append(5)
        return replace(result, statement=replace(result.statement, transactions=tuple(txns)))

    monkeypatch.setattr(select_mod, "extract_with_template", engine_with_a_bug)
    report = run_regression(db, s, templates)
    assert not report.ok
    by_doc = {r.document_id: r for r in report.results}
    assert by_doc[hdfc_id].status == "PASS"                 # the other statement is untouched
    bad = by_doc[doc_id]
    assert bad.status == "DIFF" and [c.path for c in bad.changes] == ["transactions[5].description"]
    masked = next(r for r in report.to_dict()["results"] if r["document_id"] == doc_id)["changes"][0]
    assert set(masked["new"]) <= set("aA9 ./-:,()")          # values masked by default


def test_template_that_stops_working_is_caught_even_if_fallback_matches(client, monkeypatch):
    """A wrong amount fails the balance chain, so the app falls back to the unknown-layout path."""
    doc_id, _ = _approved(client, SBI_PDF.name)
    db, s = Database(client.settings.db_path), client.settings
    templates = TemplateRegistry([COMMITTED_TEMPLATES], s.learned_templates_dir).all()
    real = select_mod.extract_with_template

    def engine_with_a_bug(doc, template, *a, **k):
        result = real(doc, template, *a, **k)
        txns = list(result.statement.transactions)
        i = next(i for i, t in enumerate(txns) if i >= 5 and t.debit is not None)
        txns[i] = replace(txns[i], debit=txns[i].debit + 1)
        return replace(result, statement=replace(result.statement, transactions=tuple(txns)))

    monkeypatch.setattr(select_mod, "extract_with_template", engine_with_a_bug)
    r = run_regression(db, s, templates).results[0]
    assert r.status == "DIFF" and r.changes[0] == Change("workflow", "known_template", "unknown_layout")


# --- the agent gate (exit criterion 2) -------------------------------------------------------------

class _HijackingProposer(Proposer):
    """Proposes a newer version of the SBI layout that drops every header rule."""
    name = "test-hijack"

    def __init__(self, template):
        self.template = template

    def propose(self, ctx):
        yield replace(self.template, version=99, header_rules=(), provenance={"created_by": "test"})


def test_agent_cannot_save_a_template_that_changes_a_golden_statement(client, tmp_path):
    _approved(client, SBI_PDF.name)
    db, s = Database(client.settings.db_path), client.settings
    registry = TemplateRegistry([COMMITTED_TEMPLATES], tmp_path / "learned-gate")
    sbi = registry.get(f"{SBI_LAYOUT}/v1")
    gate = golden_gate(db, s)
    ok, why = gate(registry.all() + [replace(sbi, version=99, header_rules=())])
    assert not ok and "1 golden" in why
    assert gate(registry.all()) == (True, "all 1 golden statements unchanged")

    saved = []
    registry.save = lambda t: saved.append(t)                   # must never be called
    out = TemplateAgent(registry, proposers=[_HijackingProposer(sbi)], golden_check=gate).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and "golden" in out.reason
    assert saved == []


def test_gate_is_open_without_golden_statements(client):
    db, s = Database(client.settings.db_path), client.settings
    assert golden_gate(db, s)([]) == (True, "no golden statements yet")
