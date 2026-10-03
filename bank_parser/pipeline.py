"""
Processing orchestration (DECISIONS.md D-011, D-012).

    upload -> store (sha256 id) -> classify -> detect layout
        known   -> template extraction -> validate -> persist
        unknown -> [Workflow 2: template agent, background pool]   (never awaited)
                   [Workflow 1: inferred-layout extraction now]  -> validate -> persist

Deterministic work runs in the extraction pool. The only agentic work --
template learning -- runs in a separate pool, so a slow or stuck learning
job can never occupy an extraction worker. When learning succeeds the
document is re-extracted with the new template and that result is stored too.
"""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
import logging
import threading
import time

from bank_parser.config import Settings
from bank_parser.detection import detect, detection_text
from bank_parser.extraction.engine import extract_with_template
from bank_parser.extraction.unknown import STRATEGY as UNKNOWN_STRATEGY, extract_unknown
from bank_parser.learning import LearningBudget, RegressionDoc, TemplateAgent
from bank_parser.pdf import MalformedPdf, PasswordRequired, PdfKind, classify, open_pdf
from bank_parser.schema import statement_to_dict
from bank_parser.storage import Database, DocumentStore
from bank_parser.templates import TemplateRegistry
from bank_parser.validation import Status, report_to_dict, validate_statement

log = logging.getLogger("bank_parser")   # never log statement content, names or numbers


class DocStatus:
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    EXTRACTED = "EXTRACTED"            # validated: arithmetic verified, nothing failed
    NEEDS_REVIEW = "NEEDS_REVIEW"      # extracted but not verified, or validation failed
    NEEDS_PASSWORD = "NEEDS_PASSWORD"
    NEEDS_OCR = "NEEDS_OCR"            # no text layer: OCR is a later phase
    FAILED = "FAILED"


def build_registry(settings):
    return TemplateRegistry(read_dirs=[settings.committed_templates_dir], write_dir=settings.learned_templates_dir)


def regression_corpus(settings, db):
    """Synthetic samples listed in the committed manifest + locally verified, unencrypted documents."""
    docs = []
    manifest = settings.committed_templates_dir / "regression_corpus.json"
    if manifest.exists():
        for entry in json.loads(manifest.read_text(encoding="utf-8")):
            path = settings.committed_templates_dir.parent / entry["path"]
            if path.exists():
                docs.append(RegressionDoc(str(path), entry["layout"]))
    store_dir = settings.documents_dir
    for row in db.verified_documents():
        if row["encrypted"]:
            continue    # passwords are never stored, so encrypted documents cannot be re-read
        bank, layout = row["template_id"].split("/")[:2]
        docs.append(RegressionDoc(str(store_dir / f"{row['id']}.pdf"), f"{bank}/{layout}"))
    return docs


def run_learning_job(settings, job_id, document_id, path, password):
    """Workflow 2. Top-level so it can run in a separate process."""
    db = Database(settings.db_path)
    db.update_template_job(job_id, status="RUNNING")
    try:
        registry = build_registry(settings)
        budget = LearningBudget(max_candidates=settings.learning_max_candidates,
                                max_refinements=settings.learning_max_refinements,
                                max_seconds=settings.learning_max_seconds)
        agent = TemplateAgent(registry, regression_corpus(settings, db), budget, backend=settings.pdf_backend)
        outcome = agent.learn(path, password, document_id)
        db.update_template_job(job_id, status=outcome.status.value, template_id=outcome.template_id,
                               reason=outcome.reason, attempts=outcome.attempts, duration_ms=outcome.duration_ms)
        return outcome.status.value, outcome.template_id
    except Exception as e:
        db.update_template_job(job_id, status="FAILED", reason=f"{type(e).__name__}: {e}")
        log.exception("template learning failed for document %s", document_id[:12])
        return "FAILED", None


class Processor:
    def __init__(self, settings=None, learning_mode="process"):
        self.settings = settings or Settings.from_env()
        s = self.settings
        s.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(s.db_path)
        self.store = DocumentStore(s.documents_dir, s.tmp_dir, s.upload_chunk_bytes, s.max_upload_bytes)
        self.registry = build_registry(s)
        self._extract_pool = ThreadPoolExecutor(s.extraction_workers, thread_name_prefix="extract")
        pool_cls = ProcessPoolExecutor if learning_mode == "process" else ThreadPoolExecutor
        self._learn_pool = pool_cls(s.learning_workers)
        self._futures = []
        self._inflight = 0          # learning jobs whose completion hook has not finished
        self._lock = threading.Lock()
        # Hook for tests and the UI: called as on_learning_done(document_id, status, template_id).
        self.on_learning_done = None

    # --- intake ---------------------------------------------------------------
    def ingest(self, read_chunk, original_name=None):
        stored = self.store.save_stream(read_chunk)
        self.db.upsert_document(stored.document_id, original_name, stored.size_bytes)
        return stored

    def submit(self, document_id, password=None):
        """Queue Workflow 1 for a stored document; returns immediately."""
        fut = self._extract_pool.submit(self.process, document_id, password)
        with self._lock:
            self._futures.append(fut)
        return fut

    # --- Workflow 1 -------------------------------------------------------------
    def process(self, document_id, password=None):
        s, db = self.settings, self.db
        path = self.store.path_for(document_id)
        db.update_document(document_id, status=DocStatus.PROCESSING, error=None)
        t0 = time.perf_counter()
        try:
            cls = classify(path, password, s.pdf_backend)
        except PasswordRequired:
            db.update_document(document_id, status=DocStatus.NEEDS_PASSWORD, encrypted=1,
                               error="PDF is password-protected; supply the password to process it")
            return DocStatus.NEEDS_PASSWORD
        except MalformedPdf as e:
            db.update_document(document_id, status=DocStatus.FAILED, error=str(e))
            return DocStatus.FAILED
        db.update_document(document_id, page_count=cls.page_count, pdf_kind=cls.kind.value,
                           encrypted=int(bool(password)))
        if cls.kind in (PdfKind.SCANNED, PdfKind.EMPTY):
            db.update_document(document_id, status=DocStatus.NEEDS_OCR if cls.kind is PdfKind.SCANNED else DocStatus.FAILED,
                               error="no text layer: requires OCR (later phase)" if cls.kind is PdfKind.SCANNED
                               else "PDF has no pages")
            return DocStatus.NEEDS_OCR if cls.kind is PdfKind.SCANNED else DocStatus.FAILED
        t_classify = time.perf_counter()

        with open_pdf(path, password, s.pdf_backend) as doc:
            text = detection_text(doc)
            self.registry.reload()      # pick up templates learned or promoted since the last document
            matches = detect(text, self.registry.all())
            t_detect = time.perf_counter()
            best = None
            for m in matches:                       # known layout: deterministic template path
                result = extract_with_template(doc, m.template, document_id, strategy="template")
                report = validate_statement(result.statement)
                if best is None or (report.status is not Status.FAIL and best[2].status is Status.FAIL):
                    best = (m.template, result, report)
                if report.status is not Status.FAIL and not result.problems:
                    break
            if best is not None and best[2].status is not Status.FAIL:
                template, result, report = best
                self._persist(document_id, "known_template", "template", template.template_id, result, report,
                              {"classify_ms": _ms(t0, t_classify), "detect_ms": _ms(t_classify, t_detect)}, cls)
                return self._finish(document_id, report, result)

            # Unknown layout (or every matching template failed): start learning first, never wait for it.
            self._start_learning(document_id, path, password)
            result, report = extract_unknown(doc, path, password, document_id)
        timings = {"classify_ms": _ms(t0, t_classify), "detect_ms": _ms(t_classify, t_detect)}
        if result.statement is None:
            db.add_extraction(document_id, "unknown_layout", UNKNOWN_STRATEGY, None,
                              {"status": "FAIL", "verified": False, "checks": []}, None,
                              [p.__dict__ for p in result.problems], dict(result.timings_ms, **timings))
            db.update_document(document_id, status=DocStatus.NEEDS_REVIEW,
                               error="no transaction table could be inferred; awaiting template learning or review")
            return DocStatus.NEEDS_REVIEW
        self._persist(document_id, "unknown_layout", UNKNOWN_STRATEGY, None, result, report, timings, cls)
        return self._finish(document_id, report, result)

    def _persist(self, document_id, workflow, strategy, template_id, result, report, timings, cls=None):
        data = statement_to_dict(result.statement)
        problems = [p.__dict__ for p in result.problems]
        if cls is not None and cls.needs_ocr_pages:
            problems.append({"code": "pages_need_ocr", "message": f"pages without text: {list(cls.needs_ocr_pages)}",
                             "page": None, "row_index": None})
        self.db.add_extraction(document_id, workflow, strategy, template_id, report_to_dict(report), data,
                               problems, dict(result.timings_ms, **timings))

    def _finish(self, document_id, report, result):
        ok = report.verified and not result.problems
        status = DocStatus.EXTRACTED if ok else DocStatus.NEEDS_REVIEW
        self.db.update_document(document_id, status=status,
                                error=None if ok else "extraction not fully verified; review required")
        return status

    # --- Workflow 2 -------------------------------------------------------------
    def _start_learning(self, document_id, path, password):
        job_id = self.db.add_template_job(document_id)
        with self._lock:
            self._inflight += 1
        fut = self._learn_pool.submit(run_learning_job, self.settings, job_id, document_id, str(path), password)
        fut.add_done_callback(lambda f: self._learning_done(document_id, password, f))
        with self._lock:
            self._futures.append(fut)
        return job_id

    def _learning_done(self, document_id, password, fut):
        try:
            try:
                status, template_id = fut.result()
            except Exception:
                status, template_id = "FAILED", None
            if status == "ACCEPTED" and template_id:
                self.registry.reload()
                # Re-extract with the new template so the stored result reflects the reusable path.
                self.submit_reextract(document_id, password, template_id)
            if self.on_learning_done:
                self.on_learning_done(document_id, status, template_id)
        finally:
            with self._lock:
                self._inflight -= 1

    def submit_reextract(self, document_id, password, template_id):
        def job():
            template = self.registry.get(template_id)
            if template is None:
                return
            with open_pdf(self.store.path_for(document_id), password, self.settings.pdf_backend) as doc:
                result = extract_with_template(doc, template, document_id, strategy="template")
            report = validate_statement(result.statement)
            self._persist(document_id, "known_template", "template", template_id, result, report, {})
            self._finish(document_id, report, result)
        fut = self._extract_pool.submit(job)
        with self._lock:
            self._futures.append(fut)

    # --- lifecycle ----------------------------------------------------------------
    def wait_idle(self, timeout=120):
        """Block until all queued work (including follow-ups) has finished. For tests and the CLI."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                pending = [f for f in self._futures if not f.done()]
                if not pending and self._inflight == 0:
                    return True
            if not pending:
                time.sleep(0.01)    # a completion hook is still queueing follow-up work
                continue
            for f in pending:
                try:
                    f.result(timeout=max(0.0, deadline - time.time()))
                except Exception:
                    pass
        return False

    def shutdown(self):
        self._extract_pool.shutdown(wait=True)
        self._learn_pool.shutdown(wait=True)


def _ms(a, b):
    return round((b - a) * 1000, 1)
