import time

from fastapi.testclient import TestClient

from bank_parser.api.main import create_app
from bank_parser.config import Settings
from tests.helpers import COMMITTED_TEMPLATES, SBI_PDF


def test_upload_and_view_statement(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "data", committed_templates_dir=COMMITTED_TEMPLATES)
    with TestClient(create_app(settings, learning_mode="thread")) as client:
        assert client.get("/").status_code == 200
        with open(SBI_PDF, "rb") as f:
            r = client.post("/api/documents", files={"file": ("statement.pdf", f, "application/pdf")})
        assert r.status_code == 200
        doc_id = r.json()["document_id"]
        for _ in range(100):
            body = client.get(f"/api/documents/{doc_id}").json()
            if body["document"]["status"] not in ("RECEIVED", "PROCESSING"):
                break
            time.sleep(0.1)
        assert body["document"]["status"] == "EXTRACTED"
        st = body["extraction"]["statement"]
        assert len(st["transactions"]) == 32 and st["header"]["bank_name"] == "State Bank of India"
        assert body["extraction"]["report"]["verified"] is True

        # Same file again: idempotent, not reprocessed.
        with open(SBI_PDF, "rb") as f:
            again = client.post("/api/documents", files={"file": ("copy.pdf", f, "application/pdf")}).json()
        assert again["document_id"] == doc_id and again["duplicate"] is True

        templates = client.get("/api/templates").json()
        assert any(t["template_id"].startswith("sbi/") for t in templates)
        assert client.get("/api/documents/" + "0" * 64).status_code == 404
