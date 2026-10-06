"""
HTTP application: upload a statement, watch it process, view the result,
and review it against the PDF (/review).

Run:  uv run uvicorn bank_parser.api.main:app --host 127.0.0.1 --port 8000

Binds to localhost by default. There is no authentication yet, so do not
expose it on a network (DECISIONS.md D-013).
"""
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from bank_parser.config import Settings
from bank_parser.pdf import MalformedPdf, PasswordRequired, open_pdf
from bank_parser.pdf.render import render_page_png
from bank_parser.pipeline import DocStatus, Processor
from bank_parser.review import PasswordVault, ReviewError, ReviewSources, SourceDir, validate_review
from bank_parser.storage import UploadTooLarge
from bank_parser.templates import template_to_dict


def create_app(settings=None, learning_mode="process"):
    settings = settings or Settings.from_env()
    state = {}
    vault = PasswordVault()
    sources = ReviewSources([
        SourceDir("private", "Private statements (sample_data/)", settings.private_samples_dir),
        SourceDir("synthetic", "Synthetic samples", settings.synthetic_samples_dir),
    ])

    @asynccontextmanager
    async def lifespan(app):
        state["processor"] = Processor(settings, learning_mode=learning_mode)
        yield
        state["processor"].shutdown()

    app = FastAPI(title="Bank statement parser", lifespan=lifespan)

    def proc():
        return state["processor"]

    def require_document(document_id):
        doc = proc().db.get_document(document_id)
        if not doc:
            raise HTTPException(404, "unknown document")
        return doc

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(settings.frontend_dir / "index.html")   # frontend/ at the repository root

    @app.get("/review", include_in_schema=False)
    def review_page():
        return FileResponse(settings.frontend_dir / "review.html")

    @app.post("/api/documents")
    async def upload(file: UploadFile = File(...), password: str | None = Form(None), reprocess: bool = Form(False)):
        p = proc()
        password = (password or "").rstrip("\r\n") or None
        try:
            # Starlette has already spooled the upload to a temporary file; copy it in chunks while hashing.
            stored = await run_in_threadpool(p.ingest, file.file.read, file.filename)
        except UploadTooLarge as e:
            raise HTTPException(413, str(e))
        vault.put(stored.document_id, password)
        doc = p.db.get_document(stored.document_id)
        done = doc["status"] in (DocStatus.EXTRACTED,)
        if not done or reprocess or (doc["status"] == DocStatus.NEEDS_PASSWORD and password):
            p.submit(stored.document_id, password or None)
        return {"document_id": stored.document_id, "duplicate": stored.already_existed,
                "size_bytes": stored.size_bytes}

    @app.post("/api/documents/{document_id}/password")
    def supply_password(document_id: str, password: str = Form(...)):
        require_document(document_id)
        password = password.rstrip("\r\n")
        vault.put(document_id, password)
        proc().submit(document_id, password)
        return {"document_id": document_id, "queued": True}

    @app.get("/api/documents")
    def list_documents():
        return [{k: d[k] for k in ("id", "original_name", "size_bytes", "page_count", "status", "created_at")}
                for d in proc().db.list_documents()]

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str):
        p = proc()
        doc = require_document(document_id)
        return {"document": doc, "extraction": p.db.latest_extraction(document_id),
                "template_job": p.db.latest_template_job(document_id)}

    @app.get("/api/templates")
    def list_templates():
        p = proc()
        p.registry.reload()
        return [{k: v for k, v in template_to_dict(t).items() if k in
                 ("template_id", "bank_name", "layout_id", "version", "status", "markers", "provenance", "regression")}
                for t in p.registry.all(include_unapproved=True)]

    # --- review ------------------------------------------------------------------------------

    @app.get("/api/review/sources")
    def review_sources():
        """Every PDF available for review: local folders first, then other uploaded documents."""
        p = proc()
        docs = {d["id"]: d for d in p.db.list_documents(limit=1000)}
        flagged = p.db.review_counts()
        out, seen = [], set()
        for s in sources.list():
            d = docs.get(s["document_id"])
            seen.add(s["document_id"])
            out.append({**s, "imported": d is not None, "status": d["status"] if d else None,
                        "page_count": d["page_count"] if d else None, "flagged": flagged.get(s["document_id"])})
        for d in docs.values():
            if d["id"] not in seen:
                out.append({"source_id": None, "group": "Uploaded", "name": d["original_name"] or d["id"][:12],
                            "size_bytes": d["size_bytes"], "document_id": d["id"], "imported": True,
                            "status": d["status"], "page_count": d["page_count"], "flagged": flagged.get(d["id"])})
        return out

    @app.post("/api/review/sources/{source_id}/import")
    def import_source(source_id: str):
        """Copy a local file into the document store (no-op if already there) and process it."""
        try:
            source_dir, path = sources.resolve(source_id)
        except ReviewError:
            raise HTTPException(404, "unknown source")
        p = proc()
        with open(path, "rb") as f:
            stored = p.ingest(f.read, path.name)
        password = sources.default_password(source_dir)
        vault.put(stored.document_id, password)
        doc = p.db.get_document(stored.document_id)
        if doc["status"] not in (DocStatus.EXTRACTED, DocStatus.NEEDS_REVIEW) or not p.db.latest_extraction(stored.document_id):
            p.submit(stored.document_id, password)
        return {"document_id": stored.document_id, "duplicate": stored.already_existed}

    @app.post("/api/documents/{document_id}/reprocess")
    def reprocess(document_id: str):
        """Extract again with the current code and templates. The result is a new extraction;
        reviews stay attached to the extraction they were made against."""
        require_document(document_id)
        password = vault.get(document_id)
        if password is None:
            for s in sources.list():
                if s["document_id"] == document_id:
                    password = sources.default_password(sources.resolve(s["source_id"])[0])
                    vault.put(document_id, password)
                    break
        proc().submit(document_id, password)
        return {"document_id": document_id, "queued": True}

    @app.post("/api/documents/{document_id}/unlock")
    def unlock(document_id: str, password: str = Form(...)):
        """Check a password and keep it in memory (never stored) so pages can be rendered."""
        require_document(document_id)
        password = password.rstrip("\r\n")
        try:
            with open_pdf(proc().store.path_for(document_id), password):
                pass
        except PasswordRequired:
            raise HTTPException(403, "wrong password")
        vault.put(document_id, password)
        return {"unlocked": True}

    @app.get("/api/documents/{document_id}/pages/{number}.png")
    def page_image(document_id: str, number: int, scale: float = 1.5):
        require_document(document_id)
        try:
            png = render_page_png(proc().store.path_for(document_id), number, vault.get(document_id), scale)
        except PasswordRequired:
            raise HTTPException(423, "password required")    # locked: unlock first
        except IndexError:
            raise HTTPException(404, "no such page")
        except MalformedPdf as e:
            raise HTTPException(422, str(e))
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})

    @app.get("/api/documents/{document_id}/review")
    def get_review(document_id: str):
        p = proc()
        require_document(document_id)
        ex = p.db.latest_extraction(document_id)
        if not ex:
            return {"extraction_id": None, "review": None}
        return {"extraction_id": ex["id"], "review": p.db.get_review(document_id, ex["id"])}

    @app.put("/api/documents/{document_id}/review")
    def save_review(document_id: str, body: dict = Body(...)):
        p = proc()
        require_document(document_id)
        ex = p.db.get_extraction(body.get("extraction_id")) if isinstance(body.get("extraction_id"), int) else None
        if not ex or ex["document_id"] != document_id:
            raise HTTPException(400, "unknown extraction for this document")
        try:
            flags, remarks = validate_review(ex["statement"], body.get("flags", []), body.get("remarks", ""))
        except ReviewError as e:
            raise HTTPException(400, str(e))
        return p.db.save_review(document_id, ex["id"], flags, remarks)

    return app


app = create_app()
