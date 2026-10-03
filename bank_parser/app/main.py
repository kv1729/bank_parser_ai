"""
HTTP application: upload a statement, watch it process, view the result.

Run:  uv run uvicorn bank_parser.app.main:app --host 127.0.0.1 --port 8000

Binds to localhost by default. There is no authentication yet, so do not
expose it on a network (DECISIONS.md D-013).
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from bank_parser.config import Settings
from bank_parser.pipeline import DocStatus, Processor
from bank_parser.storage import UploadTooLarge
from bank_parser.templates import template_to_dict

STATIC = Path(__file__).parent / "static"


def create_app(settings=None, learning_mode="process"):
    state = {}

    @asynccontextmanager
    async def lifespan(app):
        state["processor"] = Processor(settings or Settings.from_env(), learning_mode=learning_mode)
        yield
        state["processor"].shutdown()

    app = FastAPI(title="Bank statement parser", lifespan=lifespan)

    def proc():
        return state["processor"]

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/documents")
    async def upload(file: UploadFile = File(...), password: str | None = Form(None), reprocess: bool = Form(False)):
        p = proc()
        password = (password or "").rstrip("\r\n") or None
        try:
            # Starlette has already spooled the upload to a temporary file; copy it in chunks while hashing.
            stored = await run_in_threadpool(p.ingest, file.file.read, file.filename)
        except UploadTooLarge as e:
            raise HTTPException(413, str(e))
        doc = p.db.get_document(stored.document_id)
        done = doc["status"] in (DocStatus.EXTRACTED,)
        if not done or reprocess or (doc["status"] == DocStatus.NEEDS_PASSWORD and password):
            p.submit(stored.document_id, password or None)
        return {"document_id": stored.document_id, "duplicate": stored.already_existed,
                "size_bytes": stored.size_bytes}

    @app.post("/api/documents/{document_id}/password")
    def supply_password(document_id: str, password: str = Form(...)):
        p = proc()
        if not p.db.get_document(document_id):
            raise HTTPException(404, "unknown document")
        p.submit(document_id, password.rstrip("\r\n"))
        return {"document_id": document_id, "queued": True}

    @app.get("/api/documents")
    def list_documents():
        return [{k: d[k] for k in ("id", "original_name", "size_bytes", "page_count", "status", "created_at")}
                for d in proc().db.list_documents()]

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str):
        p = proc()
        doc = p.db.get_document(document_id)
        if not doc:
            raise HTTPException(404, "unknown document")
        return {"document": doc, "extraction": p.db.latest_extraction(document_id),
                "template_job": p.db.latest_template_job(document_id)}

    @app.get("/api/templates")
    def list_templates():
        p = proc()
        p.registry.reload()
        return [{k: v for k, v in template_to_dict(t).items() if k in
                 ("template_id", "bank_name", "layout_id", "version", "status", "markers", "provenance", "regression")}
                for t in p.registry.all(include_unapproved=True)]

    return app


app = create_app()
