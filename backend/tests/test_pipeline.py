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
    assert p.process(doc_id) in (DocStatus.EXTRACTED, DocStatus.NEEDS_REVIEW)
    assert p.wait_idle(30)
    assert p.db.latest_extraction(doc_id)["statement"]["transactions"]
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
