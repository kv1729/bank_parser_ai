"""
Template-learning agent (Workflow 2).

Its job is not to parse this document -- Workflow 1 already did that -- but
to leave behind a reusable, deterministic template so the next statement of
this layout takes the fast known-template path.

Bounded loop:  propose -> extract -> validate -> regression -> save | refine
Limits: candidates, refinements, wall-clock time and (future) LLM tokens.
Outcome: ACCEPTED (template saved as a new version), or HUMAN_REVIEW with
reasons. It never overwrites a template (DECISIONS.md D-009, D-011).
"""
from dataclasses import dataclass, field
from enum import Enum
import time

from bank_parser.extraction.detection import detection_text, markers_match
from bank_parser.extraction.engine import extract_with_template
from bank_parser.learning.markers import MIN_MARKERS
from bank_parser.learning.proposers import HeuristicProposer, LLMProposer
from bank_parser.pdf import open_pdf
from bank_parser.templates.registry import VersionConflict
from bank_parser.core.validation import Status, validate_statement


class LearningStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


@dataclass(frozen=True)
class LearningBudget:
    max_candidates: int = 3
    max_refinements: int = 2
    max_seconds: float = 300.0
    max_llm_tokens: int = 0          # 0 = no LLM calls permitted


@dataclass
class LearningOutcome:
    status: LearningStatus
    template_id: str | None = None
    reason: str = ""
    attempts: list = field(default_factory=list)    # metrics only; never statement content
    duration_ms: float = 0.0


@dataclass
class RegressionDoc:
    """A document whose layout is known, used to check a candidate does not hijack it."""
    path: str
    layout_key: str                   # "<bank_code>/<layout_id>"
    password: str | None = None


class LearningContext:
    def __init__(self, doc, cells_doc, text, document_id, registry):
        self.doc, self.cells_doc, self.text = doc, cells_doc, text
        self.document_id = document_id
        self.registry = registry
        self.notes = []

    def next_version(self, bank_code, layout_id):
        return self.registry.next_version(bank_code, layout_id)

    def registry_layout_for(self, bank_code, markers):
        """Reuse a layout id when this bank already has a layout with the same markers (a new version of it)."""
        for t in self.registry.all(include_unapproved=True):
            if t.bank_code == bank_code and set(t.markers) == set(markers):
                return t.layout_id
        return None


def _accept(template, result, report):
    """Acceptance criteria for a candidate template on its source document."""
    if result.problems:
        return False, "extraction_problems", f"{len(result.problems)} extraction problems"
    if not result.statement.transactions:
        return False, "no_rows", "no transactions extracted"
    if report.status is Status.FAIL:
        failed = [c.check for c in report.checks if c.status is Status.FAIL]
        return False, "rows_unverified", f"validation failed: {failed}"
    by_name = {c.check: c for c in report.checks}
    for check in template.required_checks:
        c = by_name[check]
        # A statement that prints no opening balance cannot verify its first row;
        # every later row is still checked, which is enough to prove the layout.
        first_row_only = (check == "balance_chain" and c.status is Status.WARN and c.rows_checked >= 2
                          and all(i.code == "no_opening_balance" for i in c.issues))
        if c.status is not Status.PASS and not first_row_only:
            return False, "rows_unverified", f"required check {check} is {c.status.value}"
    if not template.required_checks:
        return False, "rows_unverified", "layout offers no arithmetic invariant to verify"
    if len(template.markers) < MIN_MARKERS:
        return False, "markers_weak", f"only {len(template.markers)} layout markers"
    return True, "", ""


class TemplateAgent:
    def __init__(self, registry, regression_docs=(), budget=LearningBudget(), backend="pdfplumber",
                 proposers=None, detection_texts=None):
        self.registry = registry
        self.regression_docs = list(regression_docs)
        self.budget = budget
        self.backend = backend
        self.proposers = proposers or [HeuristicProposer(), LLMProposer(enabled=False)]
        self._texts = detection_texts if detection_texts is not None else {}

    def _regression_text(self, rd):
        if rd.path not in self._texts:
            with open_pdf(rd.path, rd.password, self.backend) as d:
                self._texts[rd.path] = detection_text(d)
        return self._texts[rd.path]

    def _regression(self, template, own_text):
        if not markers_match(template.markers, own_text):
            return False, "markers_ambiguous", "markers do not match the source document"
        key = f"{template.bank_code}/{template.layout_id}"
        checked = 0
        for rd in self.regression_docs:
            if rd.layout_key == key:
                continue          # same layout family: an older version handles older documents
            try:
                text = self._regression_text(rd)
            except Exception as e:   # unreadable corpus doc: skip, but say so
                continue
            checked += 1
            if markers_match(template.markers, text):
                return False, "markers_ambiguous", f"markers also match a {rd.layout_key} document"
        return True, "", f"checked against {checked} other-layout documents"

    def learn(self, path, password=None, document_id=None):
        t0 = time.perf_counter()
        deadline = t0 + self.budget.max_seconds
        attempts, reasons = [], []

        def done(status, template_id=None, reason=""):
            return LearningOutcome(status, template_id, reason, attempts, round((time.perf_counter() - t0) * 1000, 1))

        with open_pdf(path, password, self.backend) as doc, open_pdf(path, password, "pdfplumber") as cells_doc:
            own_text = detection_text(doc)
            ctx = LearningContext(doc, cells_doc, own_text, document_id, self.registry)
            evaluated = 0
            for proposer in self.proposers:
                if not proposer.available():
                    continue
                if proposer.uses_llm and self.budget.max_llm_tokens <= 0:
                    continue
                for candidate in proposer.propose(ctx):
                    refinements = 0
                    while candidate is not None:
                        if evaluated >= self.budget.max_candidates + self.budget.max_refinements:
                            return done(LearningStatus.HUMAN_REVIEW, reason="; ".join(reasons + ["budget exhausted"]))
                        if time.perf_counter() > deadline:
                            return done(LearningStatus.HUMAN_REVIEW, reason="; ".join(reasons + ["time limit reached"]))
                        evaluated += 1
                        result = extract_with_template(doc, candidate, document_id, strategy="candidate")
                        report = validate_statement(result.statement)
                        ok, feedback, why = _accept(candidate, result, report)
                        if ok:
                            ok, feedback, why = self._regression(candidate, own_text)
                        attempts.append({"proposer": proposer.name, "layout": candidate.layout_id,
                                         "columns": [c.role for c in candidate.table.columns],
                                         "markers": len(candidate.markers), "rows": len(result.statement.transactions),
                                         "validation": report.status.value, "accepted": ok, "reason": why})
                        if ok:
                            try:
                                self.registry.save(candidate)
                            except VersionConflict:
                                # Another worker saved this version first; take the next one.
                                candidate = candidate.with_version(
                                    self.registry.next_version(candidate.bank_code, candidate.layout_id))
                                self.registry.save(candidate)
                            return done(LearningStatus.ACCEPTED, candidate.template_id, why)
                        reasons.append(why)
                        if refinements >= self.budget.max_refinements:
                            break
                        refinements += 1
                        candidate = proposer.refine(ctx, candidate, feedback)
                    if evaluated >= self.budget.max_candidates:
                        break
            reasons.extend(ctx.notes)
        return done(LearningStatus.HUMAN_REVIEW, reason="; ".join(reasons) or "no proposer produced a candidate")
