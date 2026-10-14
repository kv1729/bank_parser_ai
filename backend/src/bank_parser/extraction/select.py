"""
How a document is extracted when its layout is known: one function shared by the
app (Workflow 1), the golden regression run and the template agent's gate, so all
three pick the same template version and produce the same output (D-022).
"""
from bank_parser.core.validation import Status, validate_statement
from bank_parser.extraction.detection import detect
from bank_parser.extraction.engine import extract_with_template


def extract_known(doc, text, templates, document_id=None):
    """
    Try every template whose markers match `text` (most specific, newest first).
    Returns (template, result, report) for the first that validates without FAIL
    and without problems, else the first non-FAIL one, or None when no template
    matches or every matching template fails validation.
    """
    best = None
    for m in detect(text, templates):
        result = extract_with_template(doc, m.template, document_id, strategy="template")
        report = validate_statement(result.statement)
        if best is None or (report.status is not Status.FAIL and best[2].status is Status.FAIL):
            best = (m.template, result, report)
        if report.status is not Status.FAIL and not result.problems:
            break
    if best is None or best[2].status is Status.FAIL:
        return None
    return best
