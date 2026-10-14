"""
Golden statements and the non-regression run (Stage 9, D-022).

A golden statement is one the owner reviewed (nothing wrong, nothing missing)
and approved in /review. Its output is stored as a snapshot. Every engine or
template change must reproduce every snapshot exactly; any difference is shown
field by field and needs a new human approval (re-approval adds a version).

- check_approvable(): the approval rules (all must hold).
- run_regression():   re-extract every golden statement as the app would and
                      compare with its snapshot. Used by scripts/regress.py,
                      the pre-push hook and the template agent's gate.

Passwords are never stored: an encrypted golden PDF is reopened with a line of
the private samples folder's `.pdf_password`.
"""
from dataclasses import dataclass, field
import json
import re
import subprocess
import time

from bank_parser.config import REPO_ROOT
from bank_parser.core.schema import statement_to_dict
from bank_parser.core.validation import report_to_dict
from bank_parser.extraction.detection import detection_text
from bank_parser.extraction.select import extract_known
from bank_parser.extraction.unknown import extract_unknown
from bank_parser.pdf import PdfError, open_pdf
from bank_parser.pdf.passwords import read_password_file


class GoldenError(ValueError):
    """An approval rule is not met; the message says which and what to do."""

    def __init__(self, message, code="refused", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


# --- snapshots and differences -------------------------------------------------------

def snapshot(statement_dict):
    """The compared part of an extraction: everything except provenance (timestamps, ids)."""
    data = json.loads(json.dumps(statement_dict))       # same types as a stored, re-read snapshot
    data.pop("provenance", None)
    return data


@dataclass(frozen=True)
class Change:
    path: str          # "header.branch", "summary.total_debits", "transactions[12].debit", "transactions"
    old: object
    new: object


def diff(old, new):
    """Field-by-field differences between two snapshots, in reading order."""
    changes = []
    for key in sorted((set(old) | set(new)) - {"header", "summary", "transactions"}):
        if old.get(key) != new.get(key):
            changes.append(Change(key, old.get(key), new.get(key)))
    for section in ("header", "summary"):
        a, b = old.get(section), new.get(section)
        if a is None or b is None:
            if a != b:
                changes.append(Change(section, a, b))
            continue
        for k in [k for k in a if k in b] + [k for k in a if k not in b] + [k for k in b if k not in a]:
            if a.get(k) != b.get(k):
                changes.append(Change(f"{section}.{k}", a.get(k), b.get(k)))
    ta, tb = old.get("transactions") or [], new.get("transactions") or []
    if len(ta) != len(tb):
        changes.append(Change("transactions", f"{len(ta)} rows", f"{len(tb)} rows"))
    for i, (x, y) in enumerate(zip(ta, tb)):
        for k in list(x) + [k for k in y if k not in x]:
            if x.get(k) != y.get(k):
                changes.append(Change(f"transactions[{i}].{k}", x.get(k), y.get(k)))
    return changes


def shape(value):
    """Mask a value for printing: letters -> a/A, digits -> 9 (punctuation and length kept)."""
    if value is None:
        return "null"
    text = value if isinstance(value, str) else json.dumps(value)
    return re.sub(r"\d", "9", re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", text)))


# --- extraction, exactly as the app does it --------------------------------------------

@dataclass
class Fresh:
    workflow: str
    template_id: str | None
    statement: dict
    report: dict


def extract_now(path, password, templates, backend, document_id=None):
    with open_pdf(path, password, backend) as doc:
        best = extract_known(doc, detection_text(doc), templates, document_id)
        if best is not None:
            template, result, report = best
            workflow, template_id = "known_template", template.template_id
        else:
            result, report = extract_unknown(doc, path, password, document_id)
            workflow, template_id = "unknown_layout", None
    if result.statement is None:
        raise GoldenError("no transaction table could be extracted", "no_output")
    return Fresh(workflow, template_id, statement_to_dict(result.statement), report_to_dict(report))


def password_candidates(settings):
    return read_password_file(settings.private_samples_dir / ".pdf_password")


def reopen(path, candidates):
    """(password_source, password) that opens the PDF; GoldenError when none does."""
    for source, password in [("none", None)] + [("password_file", p) for p in candidates]:
        try:
            with open_pdf(path, password):
                return source, password
        except PdfError:
            continue
    raise GoldenError("the PDF is encrypted and no line of sample_data/.pdf_password opens it: add its password "
                      "there (one per line) so the statement can be re-checked later", "no_password")


def warning_codes(report):
    return sorted({i["code"] for c in report.get("checks", ()) for i in c.get("issues", ()) if i.get("severity") == "WARN"})


def engine_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# --- approval -----------------------------------------------------------------------------

def check_approvable(db, settings, templates, document_id, extraction_id, acknowledge_warnings=False):
    """
    Apply the approval rules; return the keyword arguments for Database.add_golden.
    Raises GoldenError naming the first rule that fails.
    """
    latest = db.latest_extraction(document_id)
    if latest is None:
        raise GoldenError("this document has no extraction yet", "no_extraction")
    if latest["id"] != extraction_id:
        raise GoldenError(f"only the latest extraction (#{latest['id']}) can be approved", "not_latest")
    review = db.get_review(document_id, extraction_id)
    if review is None:
        raise GoldenError("save a review of this extraction first (every field checked)", "no_review")
    marked = len(review["flags"]) + len(review.get("missing") or [])
    if marked:
        raise GoldenError(f"the review still marks {marked} output(s) wrong or missing; fix or unmark them first",
                          "review_marks")
    if latest["statement"] is None or latest["validation_status"] == "FAIL":
        raise GoldenError("validation failed: a failing extraction cannot be golden", "validation_failed")
    warnings = warning_codes(latest["report"] or {})
    if warnings and not acknowledge_warnings:
        raise GoldenError(f"validation has {len(warnings)} warning(s); confirm you checked them", "warnings",
                          {"warnings": warnings})
    path = settings.documents_dir / f"{document_id}.pdf"
    source, password = reopen(path, password_candidates(settings))
    fresh = extract_now(path, password, templates, settings.pdf_backend, document_id)
    changes = diff(snapshot(latest["statement"]), snapshot(fresh.statement))
    if changes:
        raise GoldenError(f"the output changed since your review ({len(changes)} field(s)): re-extract and review "
                          "again", "stale", {"fields": [c.path for c in changes][:50]})
    return dict(extraction_id=extraction_id, snapshot=snapshot(fresh.statement), workflow=fresh.workflow,
                template_id=fresh.template_id, backend=settings.pdf_backend, engine_commit=engine_commit(),
                validation_status=latest["validation_status"], warnings=warnings, password_source=source)


# --- regression ---------------------------------------------------------------------------

@dataclass
class GoldenResult:
    document_id: str
    version: int
    backend: str
    status: str                       # PASS | DIFF | ERROR
    changes: list = field(default_factory=list)
    template_then: str | None = None
    template_now: str | None = None
    error: str | None = None
    ms: float = 0.0


@dataclass
class RegressionReport:
    results: list
    backend: str
    extra_backends: tuple = ()

    @property
    def failed(self):
        """Only the gate backend decides; other backends are informational."""
        return [r for r in self.results if r.backend == self.backend and r.status != "PASS"]

    @property
    def ok(self):
        return not self.failed

    def to_dict(self, show_values=False):
        fmt = (lambda v: v) if show_values else shape
        return {"backend": self.backend, "extra_backends": list(self.extra_backends), "ok": self.ok,
                "results": [{"document_id": r.document_id, "version": r.version, "backend": r.backend,
                             "status": r.status, "template_then": r.template_then, "template_now": r.template_now,
                             "error": r.error, "ms": r.ms,
                             "changes": [{"path": c.path, "old": fmt(c.old), "new": fmt(c.new)} for c in r.changes]}
                            for r in self.results]}


def run_regression(db, settings, templates, extra_backends=(), goldens=None):
    """
    Re-extract every active golden statement with `templates` (the registry's, or
    the registry's plus a candidate) and compare with its snapshot.
    """
    goldens = db.active_goldens() if goldens is None else goldens
    candidates = password_candidates(settings)
    results = []
    for backend in (settings.pdf_backend, *extra_backends):
        for g in goldens:
            t0 = time.perf_counter()
            r = GoldenResult(g["document_id"], g["version"], backend, "ERROR", template_then=g["template_id"])
            try:
                path = settings.documents_dir / f"{g['document_id']}.pdf"
                if not path.is_file():
                    raise GoldenError("the PDF is no longer in data/documents", "missing_pdf")
                _, password = reopen(path, candidates)
                fresh = extract_now(path, password, templates, backend, g["document_id"])
                r.template_now = fresh.template_id
                r.changes = diff(g["snapshot"], snapshot(fresh.statement))
                # A known layout that falls back to the unknown-layout path is a regression even
                # when the fallback happens to give the same output (its template stopped working).
                # A newer template version with identical output is fine (template_now shows it).
                if fresh.workflow != g["workflow"]:
                    r.changes.insert(0, Change("workflow", g["workflow"], fresh.workflow))
                r.status = "DIFF" if r.changes else "PASS"
            except GoldenError as e:
                r.error = str(e)
            except Exception as e:                  # never echo exception text: it may contain content
                r.error = type(e).__name__
            r.ms = round((time.perf_counter() - t0) * 1000, 1)
            results.append(r)
    return RegressionReport(results, settings.pdf_backend, tuple(extra_backends))


def golden_gate(db, settings):
    """A callable for the template agent: (ok, reason) for a set of templates."""
    def check(templates):
        goldens = db.active_goldens()
        if not goldens:
            return True, "no golden statements yet"
        report = run_regression(db, settings, templates, goldens=goldens)
        if report.ok:
            return True, f"all {len(goldens)} golden statements unchanged"
        return False, f"would change the output of {len(report.failed)} golden statement(s)"
    return check
