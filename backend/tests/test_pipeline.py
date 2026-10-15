import threading

import pytest

import bank_parser.pipeline as pipeline
from bank_parser.config import Settings
from bank_parser.pipeline import DocStatus, Processor
from bank_parser.storage import DocumentStore, UploadTooLarge
from tests.helpers import COMMITTED_TEMPLATES, HDFC_PDF, SBI_PDF, have_pymupdf


def _processor(tmp_path, templates_dir):
    s = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=templates_dir,
                          pdf_backend="pdfplumber")
    return Processor(s, learning_mode="thread")


def _ingest(p, path):
    with open(path, "rb") as f:
        return p.ingest(f.read, path.name).document_id


def test_known_layout_takes_template_path_without_learning(tmp_path):
    p = _processor(tmp_path, COMMITTED_TEMPLATES)
    doc_id = _ingest(p, SBI_PDF)
    assert p.process(doc_id) == DocStatus.EXTRACTED
    ex = p.db.latest_extraction(doc_id)
    assert ex["workflow"] == "known_template" and ex["template_id"].startswith("sbi/")
    assert ex["verified"] == 1 and len(ex["statement"]["transactions"]) == 32
    assert p.db.latest_template_job(doc_id) is None
    p.shutdown()


def test_unknown_layout_extraction_does_not_wait_for_learning(tmp_path, monkeypatch):
    """The central requirement: Workflow 1 completes while Workflow 2 is still running."""
    release, started = threading.Event(), threading.Event()
    real_job = pipeline.run_learning_job

    def slow_learning(*args):
        started.set()
        assert release.wait(30)
        return real_job(*args)

    monkeypatch.setattr(pipeline, "run_learning_job", slow_learning)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, SBI_PDF)

    status = p.process(doc_id)                   # returns although learning is blocked
    assert started.wait(10)
    assert status == DocStatus.EXTRACTED
    ex = p.db.latest_extraction(doc_id)
    assert ex["workflow"] == "unknown_layout" and ex["verified"] == 1
    assert p.db.latest_template_job(doc_id)["status"] in ("QUEUED", "RUNNING")

    release.set()
    assert p.wait_idle(60)
    job = p.db.latest_template_job(doc_id)
    assert job["status"] == "ACCEPTED" and job["template_id"].startswith("sbi/")
    # After learning, the document was re-extracted with the new template...
    assert p.db.latest_extraction(doc_id)["workflow"] == "known_template"
    # ...and the next statement of this layout takes the known path directly.
    assert p.process(doc_id) == DocStatus.EXTRACTED
    assert p.db.latest_extraction(doc_id)["template_id"] == job["template_id"]
    p.shutdown()


def test_learning_failure_never_breaks_extraction(tmp_path, monkeypatch):
    def broken(settings, job_id, *a):
        raise RuntimeError("learner crashed")
    monkeypatch.setattr(pipeline, "run_learning_job", broken)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, HDFC_PDF)
    assert p.process(doc_id) in (DocStatus.EXTRACTED, DocStatus.LEARNING)
    assert p.wait_idle(30)
    assert p.db.latest_extraction(doc_id)["statement"]["transactions"]
    # The crash closes the job and the document no longer waits for it.
    assert p.db.latest_template_job(doc_id)["status"] == "FAILED"
    assert p.db.get_document(doc_id)["status"] in (DocStatus.EXTRACTED, DocStatus.NEEDS_REVIEW)
    p.shutdown()


def test_ingest_is_content_addressed_and_idempotent(tmp_path):
    p = _processor(tmp_path, COMMITTED_TEMPLATES)
    with open(SBI_PDF, "rb") as f:
        a = p.ingest(f.read, "a.pdf")
    with open(SBI_PDF, "rb") as f:
        b = p.ingest(f.read, "b.pdf")
    with open(HDFC_PDF, "rb") as f:
        c = p.ingest(f.read, "c.pdf")
    assert a.document_id == b.document_id and b.already_existed and not a.already_existed
    assert c.document_id != a.document_id
    assert len(p.db.list_documents()) == 2
    p.shutdown()


def test_streaming_store_uses_small_chunks_and_optional_guard(tmp_path):
    reads = []
    data = b"%PDF-" + b"x" * 10_000
    pos = [0]

    def read(n):
        reads.append(n)
        chunk = data[pos[0]:pos[0] + n]
        pos[0] += n
        return chunk

    store = DocumentStore(tmp_path / "docs", tmp_path / "tmp", chunk_bytes=1024)
    stored = store.save_stream(read)
    assert stored.size_bytes == len(data) and set(reads) == {1024}
    guarded = DocumentStore(tmp_path / "docs2", tmp_path / "tmp2", chunk_bytes=1024, max_bytes=2048)
    pos[0] = 0
    with pytest.raises(UploadTooLarge):
        guarded.save_stream(read)
    assert list((tmp_path / "tmp2").iterdir()) == []      # partial upload cleaned up


def test_no_size_cap_by_default():
    assert Settings.from_env().max_upload_bytes is None


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build test PDFs")
def test_password_flow(tmp_path):
    import pymupdf
    doc = pymupdf.open(SBI_PDF)
    locked = tmp_path / "locked.pdf"
    doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="o")
    doc.close()
    p = _processor(tmp_path, COMMITTED_TEMPLATES)
    doc_id = _ingest(p, locked)
    assert p.process(doc_id) == DocStatus.NEEDS_PASSWORD
    assert p.process(doc_id, "pw123") == DocStatus.EXTRACTED
    p.shutdown()


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build test PDFs")
def test_scanned_pdf_flagged_for_ocr(tmp_path):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(20, 20, 300, 300), fill=(0.2, 0.2, 0.2))
    scanned = tmp_path / "scan.pdf"
    doc.save(scanned)
    doc.close()
    p = _processor(tmp_path, COMMITTED_TEMPLATES)
    doc_id = _ingest(p, scanned)
    assert p.process(doc_id) == DocStatus.NEEDS_OCR
    assert p.db.get_document(doc_id)["pdf_kind"] == "SCANNED"
    p.shutdown()


# --- Stage 10.3: LEARNING status, then the checked result (D-023) -----------------------------

def _no_table(*args, **kwargs):
    """Workflow 1 finds no table (as for an unknown layout it cannot read)."""
    from bank_parser.extraction.engine import ExtractionResult
    from bank_parser.extraction.table import Problem
    return ExtractionResult(None, (Problem("no_layout", "no transaction table could be inferred"),), {}), None


def test_unreadable_layout_shows_learning_then_the_learned_result(tmp_path, monkeypatch):
    release = threading.Event()
    real_job = pipeline.run_learning_job

    def slow_learning(*args):
        assert release.wait(30)
        return real_job(*args)

    monkeypatch.setattr(pipeline, "extract_unknown", _no_table)
    monkeypatch.setattr(pipeline, "run_learning_job", slow_learning)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, SBI_PDF)

    assert p.process(doc_id) == DocStatus.LEARNING          # returns at once, learning still blocked
    doc = p.db.get_document(doc_id)
    assert doc["status"] == DocStatus.LEARNING and "learning this layout" in doc["error"]

    release.set()
    assert p.wait_idle(60)
    assert p.db.latest_template_job(doc_id)["status"] == "ACCEPTED"
    assert p.db.latest_extraction(doc_id)["workflow"] == "known_template"
    assert p.db.get_document(doc_id)["status"] == DocStatus.EXTRACTED
    p.shutdown()


def test_learned_result_is_never_overtaken_by_the_first_attempt(tmp_path, monkeypatch):
    """Learning starts only after Workflow 1 stored its result, so a learner that finishes at once
    still leaves the template extraction as the latest one."""
    monkeypatch.setattr(pipeline, "extract_unknown", _no_table)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, SBI_PDF)
    p.process(doc_id)
    assert p.wait_idle(60)
    assert p.db.latest_extraction(doc_id)["workflow"] == "known_template"
    p.shutdown()


def test_learning_that_fails_turns_into_needs_review_with_the_reason(tmp_path, monkeypatch):
    def gives_up(settings, job_id, *args):
        from bank_parser.storage import Database
        Database(settings.db_path).update_template_job(job_id, status="HUMAN_REVIEW", reason="no proposer produced a candidate")
        return "HUMAN_REVIEW", None

    monkeypatch.setattr(pipeline, "extract_unknown", _no_table)
    monkeypatch.setattr(pipeline, "run_learning_job", gives_up)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, SBI_PDF)
    assert p.process(doc_id) == DocStatus.LEARNING
    assert p.wait_idle(30)
    doc = p.db.get_document(doc_id)
    assert doc["status"] == DocStatus.NEEDS_REVIEW and "no proposer produced a candidate" in doc["error"]
    p.shutdown()


def test_verified_first_attempt_is_extracted_at_once_and_learning_stays_silent(tmp_path, monkeypatch):
    def gives_up(settings, job_id, *args):
        return "HUMAN_REVIEW", None
    monkeypatch.setattr(pipeline, "run_learning_job", gives_up)
    p = _processor(tmp_path, tmp_path / "no-templates")
    doc_id = _ingest(p, SBI_PDF)                  # ruled synthetic table: Workflow 1 verifies it
    assert p.process(doc_id) == DocStatus.EXTRACTED
    assert p.wait_idle(30)
    assert p.db.get_document(doc_id)["status"] == DocStatus.EXTRACTED
    p.shutdown()


def test_reupload_while_learning_starts_no_second_job(tmp_path):
    from fastapi.testclient import TestClient
    from bank_parser.api.main import create_app
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=tmp_path / "no-templates")
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        doc_id = _ingest_via_api(client)
        db = pipeline.Database(settings.db_path)
        assert _settle(client, doc_id)
        jobs_before = _job_count(db, doc_id)
        db.update_document(doc_id, status=DocStatus.LEARNING)
        _ingest_via_api(client)
        assert client.post(f"/api/documents/{doc_id}/reprocess").status_code == 409
        assert _job_count(db, doc_id) == jobs_before


def test_interrupted_work_is_marked_at_start(tmp_path):
    db = pipeline.Database(tmp_path / "db.sqlite")
    db.upsert_document("a" * 64, "x.pdf", 1)
    db.update_document("a" * 64, status=DocStatus.LEARNING)
    job = db.add_template_job("a" * 64)
    assert db.mark_interrupted() == ["a" * 64]
    assert db.get_document("a" * 64)["status"] == DocStatus.NEEDS_REVIEW
    assert db.latest_template_job("a" * 64)["status"] == "FAILED"


def _ingest_via_api(client):
    with open(SBI_PDF, "rb") as f:
        return client.post("/api/documents", files={"file": ("s.pdf", f, "application/pdf")}).json()["document_id"]


def _settle(client, doc_id):
    import time
    for _ in range(300):
        body = client.get(f"/api/documents/{doc_id}").json()
        job = body.get("template_job") or {}
        if body["document"]["status"] not in ("RECEIVED", "PROCESSING", "LEARNING") and job.get("status") not in ("QUEUED", "RUNNING"):
            return True
        time.sleep(0.1)
    return False


def _job_count(db, doc_id):
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM template_jobs WHERE document_id = ?", (doc_id,)).fetchone()[0]
