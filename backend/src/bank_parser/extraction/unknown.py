"""
Workflow 1 for documents no template matches: extract immediately using
deterministic layout inference, without waiting for template learning.
Ruled tables come from their cell geometry; tables without ruled lines from
word geometry and familiar heading words (learning/llm_view.py). The local
model is never used here: it runs only in the background agent (D-023).
"""
from dataclasses import replace
import time

from bank_parser.extraction.engine import ExtractionResult, extract_with_template
from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES, identify_bank
from bank_parser.extraction.inference import infer_layouts
from bank_parser.extraction.table import Problem
from bank_parser.learning import llm_view
from bank_parser.pdf import open_pdf
from bank_parser.templates.model import Template
from bank_parser.core.validation import Status, validate_statement

STRATEGY = "generic_layout_inference"


def extract_unknown(doc, path, password, document_id=None):
    """
    Returns (ExtractionResult, ValidationReport). Candidates are tried in order
    and the first whose arithmetic verifies wins; otherwise the candidate with
    the fewest failing checks is returned, clearly unverified.
    """
    t0 = time.perf_counter()
    with open_pdf(path, password, "pdfplumber") as cells_doc:   # ruled-cell geometry needs pdfplumber
        candidates = infer_layouts(cells_doc, doc)
    t1 = time.perf_counter()
    specs = [cand.spec for cand in candidates] or _unruled_specs(doc)
    if not specs:
        empty = ExtractionResult(None, (Problem("no_layout", "no transaction table could be inferred"),),
                                 {"infer": round((time.perf_counter() - t0) * 1000, 1)})
        return empty, None
    best = None
    for spec in specs:
        result = _adhoc_extract(doc, spec, document_id)
        report = validate_statement(result.statement)
        fails = sum(1 for c in report.checks if c.status is Status.FAIL) + len(result.problems)
        if best is None or fails < best[0]:
            best = (fails, result, report)
        if report.verified and not result.problems:
            break
    _, result, report = best
    timings = dict(result.timings_ms, infer=round((t1 - t0) * 1000, 1))
    return ExtractionResult(result.statement, result.problems, timings, result.field_sources), report


def _adhoc_extract(doc, spec, document_id):
    adhoc = Template("unknown", "Unknown bank", "adhoc", 1, (), spec, GENERIC_HEADER_RULES)
    result = extract_with_template(doc, adhoc, document_id, strategy=STRATEGY)
    # An ad-hoc template has no identity of its own: take the bank from the account's IFSC.
    _, name = identify_bank(result.statement.header.ifsc)
    st = result.statement
    st = replace(st, header=replace(st.header, bank_name=name),
                 provenance=replace(st.provenance, template_id=None, template_version=None))
    return ExtractionResult(st, result.problems, result.timings_ms, result.field_sources)


def _unruled_specs(doc):
    """A table without ruled lines whose heading words name the columns (no model), or []."""
    try:
        view = llm_view.find_first_table(doc)
        roles = llm_view.word_roles(view)
        return [llm_view.layout_from_roles(doc, view, *roles).spec] if roles else []
    except llm_view.AnswerError:
        return []


def extract_unruled(doc, document_id=None):
    """Workflow 1 for an unruled table alone: (ExtractionResult, report), or (None, None)."""
    specs = _unruled_specs(doc)
    if not specs:
        return None, None
    result = _adhoc_extract(doc, specs[0], document_id)
    return result, validate_statement(result.statement)
