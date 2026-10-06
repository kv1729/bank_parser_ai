"""Review screen API (Stage 7): sources, import, page rendering, password vault, saving reviews."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from bank_parser.api.main import create_app
from bank_parser.config import Settings
from tests.helpers import COMMITTED_TEMPLATES, HDFC_PDF, SBI_PDF, have_pymupdf

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def client(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    synthetic = tmp_path / "synthetic"
    synthetic.mkdir()
    shutil.copy(SBI_PDF, synthetic / SBI_PDF.name)
    shutil.copy(HDFC_PDF, synthetic / HDFC_PDF.name)
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES,
                                 private_samples_dir=private, synthetic_samples_dir=synthetic)
    with TestClient(create_app(settings, learning_mode="thread")) as c:
        c.private_dir = private
        yield c


def _wait(client, doc_id):
    for _ in range(150):
        body = client.get(f"/api/documents/{doc_id}").json()
        if body["document"]["status"] not in ("RECEIVED", "PROCESSING") and body["extraction"]:
            return body
        time.sleep(0.1)
    raise AssertionError("document did not finish processing")


def _import(client, name):
    src = next(s for s in client.get("/api/review/sources").json() if s["name"] == name)
    r = client.post(f"/api/review/sources/{src['source_id']}/import")
    assert r.status_code == 200
    return r.json()["document_id"]


def test_review_page_served(client):
    r = client.get("/review")
    assert r.status_code == 200 and "Statement review" in r.text


def test_sources_list_local_files_before_import(client):
    sources = client.get("/api/review/sources").json()
    names = {s["name"]: s for s in sources}
    assert {SBI_PDF.name, HDFC_PDF.name} <= set(names)
    assert names[SBI_PDF.name]["imported"] is False and names[SBI_PDF.name]["group"] == "Synthetic samples"


def test_import_processes_and_is_idempotent(client):
    doc_id = _import(client, SBI_PDF.name)
    body = _wait(client, doc_id)
    assert body["document"]["status"] == "EXTRACTED"
    assert _import(client, SBI_PDF.name) == doc_id          # second import: same document
    src = next(s for s in client.get("/api/review/sources").json() if s["name"] == SBI_PDF.name)
    assert src["imported"] and src["page_count"] == 2


@pytest.mark.parametrize("bad", ["synthetic:../../secret.pdf", "synthetic:missing.pdf", "nope:x.pdf", "synthetic:"])
def test_import_rejects_unknown_or_traversing_sources(client, bad):
    assert client.post(f"/api/review/sources/{bad}/import").status_code == 404


def test_pages_render_as_png(client):
    doc_id = _import(client, HDFC_PDF.name)
    _wait(client, doc_id)
    r = client.get(f"/api/documents/{doc_id}/pages/1.png?scale=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(PNG_MAGIC)
    assert client.get(f"/api/documents/{doc_id}/pages/2.png").status_code == 404


def test_review_round_trip_and_validation(client):
    doc_id = _import(client, SBI_PDF.name)
    body = _wait(client, doc_id)
    ex_id = body["extraction"]["id"]
    assert client.get(f"/api/documents/{doc_id}/review").json() == {"extraction_id": ex_id, "review": None}

    payload = {"extraction_id": ex_id, "flags": ["header.account_holder_address", "txn.14", "txn.14"],
               "remarks": "Row 15 description looks truncated."}
    saved = client.put(f"/api/documents/{doc_id}/review", json=payload).json()
    assert saved["flags"] == ["header.account_holder_address", "txn.14"]
    assert client.get(f"/api/documents/{doc_id}/review").json()["review"]["remarks"].startswith("Row 15")

    # Saving again updates the same review instead of creating another.
    again = client.put(f"/api/documents/{doc_id}/review", json={**payload, "flags": []}).json()
    assert again["id"] == saved["id"] and again["flags"] == []
    src = next(s for s in client.get("/api/review/sources").json() if s["document_id"] == doc_id)
    assert src["flagged"] == 0

    for bad in ({"extraction_id": ex_id, "flags": ["txn.999"]}, {"extraction_id": ex_id, "flags": ["header.password"]},
                {"extraction_id": ex_id + 999, "flags": []}, {"extraction_id": ex_id, "flags": "txn.1"}):
        assert client.put(f"/api/documents/{doc_id}/review", json=bad).status_code == 400


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build an encrypted test PDF")
def test_encrypted_pdf_uses_folder_password_and_unlock(client):
    import pymupdf
    doc = pymupdf.open(SBI_PDF)
    doc.save(client.private_dir / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="o")
    doc.close()
    (client.private_dir / ".pdf_password").write_text("pw123\n", encoding="utf-8")
    doc_id = _import(client, "locked.pdf")
    assert _wait(client, doc_id)["document"]["status"] == "EXTRACTED"     # folder password used, in memory
    assert client.get(f"/api/documents/{doc_id}/pages/1.png").status_code == 200
    assert client.post(f"/api/documents/{doc_id}/unlock", data={"password": "wrong"}).status_code == 403
    assert client.post(f"/api/documents/{doc_id}/unlock", data={"password": "pw123"}).status_code == 200


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build an encrypted test PDF")
def test_locked_pages_need_unlock(client):
    import pymupdf
    doc = pymupdf.open(HDFC_PDF)
    doc.save(client.private_dir / "nopw.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="s3", owner_pw="o")
    doc.close()
    doc_id = _import(client, "nopw.pdf")                                   # no .pdf_password in the folder
    for _ in range(100):
        if client.get(f"/api/documents/{doc_id}").json()["document"]["status"] == "NEEDS_PASSWORD":
            break
        time.sleep(0.1)
    assert client.get(f"/api/documents/{doc_id}/pages/1.png").status_code == 423
    assert client.post(f"/api/documents/{doc_id}/unlock", data={"password": "s3"}).status_code == 200
    assert client.get(f"/api/documents/{doc_id}/pages/1.png").status_code == 200
