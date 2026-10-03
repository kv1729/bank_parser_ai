"""
Workflow 1 for documents no template matches: extract immediately using
deterministic layout inference, without waiting for template learning.
"""
from dataclasses import replace
import time

from bank_parser.extraction.engine import ExtractionResult, extract_with_template
from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES, identify_bank
from bank_parser.extraction.inference import infer_layouts
from bank_parser.extraction.table import Problem
from bank_parser.pdf import open_pdf
from bank_parser.templates.model import Template
from bank_parser.validation import Status, validate_statement

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
    if not candidates:
        empty = ExtractionResult(None, (Problem("no_layout", "no transaction table could be inferred"),),
                                 {"infer": round((t1 - t0) * 1000, 1)})
        return empty, None
    best = None
    for cand in candidates:
        adhoc = Template("unknown", "Unknown bank", "adhoc", 1, (), cand.spec, GENERIC_HEADER_RULES)
        result = extract_with_template(doc, adhoc, document_id, strategy=STRATEGY)
        # An ad-hoc template has no identity of its own: take the bank from the account's IFSC.
        _, name = identify_bank(result.statement.header.ifsc)
        st = result.statement
        st = replace(st, header=replace(st.header, bank_name=name),
                     provenance=replace(st.provenance, template_id=None, template_version=None))
        result = ExtractionResult(st, result.problems, result.timings_ms)
        report = validate_statement(result.statement)
        fails = sum(1 for c in report.checks if c.status is Status.FAIL) + len(result.problems)
        if best is None or fails < best[0]:
            best = (fails, result, report)
        if report.verified and not result.problems:
            break
    _, result, report = best
    timings = dict(result.timings_ms, infer=round((t1 - t0) * 1000, 1))
    return ExtractionResult(result.statement, result.problems, timings), report
