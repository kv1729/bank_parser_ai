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

from bank_parser.autonomy import track_record
from bank_parser.config import Settings
from bank_parser.golden import GoldenError, check_approvable, diff, password_candidates, snapshot
from bank_parser.pdf import MalformedPdf, PasswordRequired, open_pdf
from bank_parser.pdf.render import render_page_png
from bank_parser.pipeline import DocStatus, Processor
from bank_parser.review import PasswordVault, ReviewError, ReviewSources, SourceDir, validate_agent_feedback, validate_review
from bank_parser.learning.examples import record_golden_file, record_review
from bank_parser.self_review import apply_history, build_self_review, calibration, ensure_confidence
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
        state["processor"].db.mark_interrupted()      # work from before a restart never finishes
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
        # Already being processed or learned: a second upload must not start a second learning job.
        busy = doc["status"] in (DocStatus.PROCESSING, DocStatus.LEARNING)
        if not busy and (not done or reprocess or (doc["status"] == DocStatus.NEEDS_PASSWORD and password)):
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

    def with_self_review(ex):
        """Extractions stored before self-reviews existed get one computed now (field sources unknown)."""
        if ex and ex.get("self_review") is None and ex.get("statement") is not None:
            p = proc()
            template = p.registry.get(ex["template_id"]) if ex.get("template_id") else None
            job = p.db.latest_template_job(ex["document_id"])
            if job and job.get("template_id") != ex.get("template_id"):
                job = None              # this document's job did not make this template
            ex["self_review"] = apply_history(build_self_review(ex["statement"], ex["report"], None, template, job),
                                              p.db.example_history(ex.get("template_id")), ex["id"])
        elif ex and ex.get("self_review") is not None:
            template = proc().registry.get(ex["template_id"]) if ex.get("template_id") else None
            ensure_confidence(ex["self_review"], ex["statement"], ex["report"], template)
        return ex

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str):
        p = proc()
        doc = require_document(document_id)
        return {"document": doc, "extraction": with_self_review(p.db.latest_extraction(document_id)),
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
        golden = p.db.golden_versions()
        out, seen = [], set()
        for s in sources.list():
            d = docs.get(s["document_id"])
            seen.add(s["document_id"])
            out.append({**s, "imported": d is not None, "status": d["status"] if d else None,
                        "page_count": d["page_count"] if d else None, "flagged": flagged.get(s["document_id"]),
                        "golden_version": golden.get(s["document_id"])})
        for d in docs.values():
            if d["id"] not in seen:
                out.append({"source_id": None, "group": "Uploaded", "name": d["original_name"] or d["id"][:12],
                            "size_bytes": d["size_bytes"], "document_id": d["id"], "imported": True,
                            "status": d["status"], "page_count": d["page_count"], "flagged": flagged.get(d["id"]),
                            "golden_version": golden.get(d["id"])})
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
        password = sources.default_password(source_dir, path)
        vault.put(stored.document_id, password)
        doc = p.db.get_document(stored.document_id)
        settled = doc["status"] in (DocStatus.EXTRACTED, DocStatus.NEEDS_REVIEW) and p.db.latest_extraction(stored.document_id)
        if not settled and doc["status"] not in (DocStatus.PROCESSING, DocStatus.LEARNING):
            p.submit(stored.document_id, password)
        return {"document_id": stored.document_id, "duplicate": stored.already_existed}

    @app.post("/api/documents/{document_id}/reprocess")
    def reprocess(document_id: str):
        """Extract again with the current code and templates. The result is a new extraction;
        reviews stay attached to the extraction they were made against."""
        doc = require_document(document_id)
        if doc["status"] in (DocStatus.PROCESSING, DocStatus.LEARNING):
            raise HTTPException(409, "this statement is still being processed or learned; try again when it is done")
        password = vault.get(document_id)
        if password is None:
            for s in sources.list():
                if s["document_id"] == document_id:
                    source_dir, source_path = sources.resolve(s["source_id"])
                    password = sources.default_password(source_dir, source_path)
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
            return {"extraction_id": None, "review": None, "previous": None}
        review = p.db.get_review(document_id, ex["id"])
        # A re-extraction starts without a review; show the last one so it is not lost from view.
        previous = None if review else p.db.previous_review(document_id, ex["id"])
        self_review = with_self_review(ex).get("self_review")
        return {"extraction_id": ex["id"], "review": review, "previous": previous,
                "self_review": self_review, "calibration": calibration(self_review, review)}

    @app.put("/api/documents/{document_id}/review")
    def save_review(document_id: str, body: dict = Body(...)):
        p = proc()
        require_document(document_id)
        ex = p.db.get_extraction(body.get("extraction_id")) if isinstance(body.get("extraction_id"), int) else None
        if not ex or ex["document_id"] != document_id:
            raise HTTPException(400, "unknown extraction for this document")
        try:
            flags, remarks, missing = validate_review(ex["statement"], body.get("flags", []), body.get("remarks", ""),
                                                      body.get("missing", []))
            feedback = None
            if "agent_feedback" in body:
                items = (with_self_review(ex).get("self_review") or {}).get("unsure", [])
                feedback = validate_agent_feedback(body["agent_feedback"], {i["id"] for i in items})
        except ReviewError as e:
            raise HTTPException(400, str(e))
        saved = p.db.save_review(document_id, ex["id"], flags, remarks, missing, feedback)
        # Training examples (D-027): what was marked, and the verdicts; never fails the save.
        record_review(p.db, document_id, ex["id"], ex.get("template_id"), saved["flags"], saved["missing"],
                      saved["agent_feedback"], with_self_review(ex).get("self_review"), saved["updated_at"])
        return saved

    @app.get("/api/autonomy")
    def autonomy_status():
        """Each layout's track record with the owner (Stage 11, D-029). Counts only."""
        return {"threshold": settings.autonomy_threshold, "enabled": settings.autonomy,
                "layouts": track_record(proc().db, settings.autonomy_threshold)}

    @app.get("/api/training-examples/summary")
    def training_examples_summary():
        """How many examples the agent has learned from the owner (counts only)."""
        counts = proc().db.example_counts()
        return {"total": sum(counts.values()), "by_kind": counts}

    # --- golden statements (Stage 9, D-022) ---------------------------------------------------

    def golden_info(g):
        return None if g is None else {k: g[k] for k in ("version", "extraction_id", "workflow", "template_id",
                                                         "backend", "engine_commit", "validation_status", "warnings",
                                                         "approved_at")}

    @app.get("/api/documents/{document_id}/golden")
    def get_golden(document_id: str):
        """The active golden snapshot and how the latest extraction differs from it."""
        p = proc()
        require_document(document_id)
        g, ex = p.db.golden_for(document_id), p.db.latest_extraction(document_id)
        changes = None
        if g is not None and ex is not None and ex["statement"] is not None:
            changes = [{"path": c.path, "old": c.old, "new": c.new} for c in diff(g["snapshot"], snapshot(ex["statement"]))]
        return {"golden": golden_info(g), "latest_extraction_id": ex["id"] if ex else None, "differs": changes}

    @app.post("/api/documents/{document_id}/golden")
    def approve_golden(document_id: str, body: dict = Body(...)):
        """Approve the latest extraction as golden (only if every approval rule holds)."""
        p = proc()
        require_document(document_id)
        extraction_id = body.get("extraction_id")
        if not isinstance(extraction_id, int):
            raise HTTPException(400, "extraction_id must be an integer")
        p.registry.reload()
        try:
            kwargs = check_approvable(p.db, settings, p.registry.all(), document_id, extraction_id,
                                      acknowledge_warnings=body.get("acknowledge_warnings") is True)
        except GoldenError as e:
            raise HTTPException(409, {"code": e.code, "message": str(e), **e.details})
        golden = p.db.add_golden(document_id, **kwargs)
        learn_from_golden(p, document_id, golden)
        return {"golden": golden_info(golden)}

    def learn_from_golden(p, document_id, golden):
        """Training examples from an approval (D-027). Never fails the approval."""
        template = p.registry.get(golden["template_id"]) if golden.get("template_id") else None
        known = [pw for pw in (vault.get(document_id),) if pw]
        return record_golden_file(p.db, p.store.path_for(document_id), golden, template,
                                  known + list(password_candidates(settings)), settings.pdf_backend)

    return app


app = create_app()
