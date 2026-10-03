"""Run a template against a document: the deterministic known-layout path."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time

from bank_parser.extraction.header import apply_header_rules, resolve_pages
from bank_parser.extraction.table import Problem, assemble_rows, rows_to_transactions
from bank_parser.schema import Provenance, Statement, StatementHeader, StatementSummary


@dataclass(frozen=True)
class ExtractionResult:
    statement: Statement | None
    problems: tuple[Problem, ...] = ()
    timings_ms: dict = field(default_factory=dict)


def _pages_needed_by_rules(rules, page_count):
    needed = set()
    for r in rules:
        needed.update(resolve_pages(r.pages, page_count))
    return needed


def build_statement(values, transactions, template=None, *, bank_code=None, bank_name=None,
                    document_id=None, strategy=None, backend=None, page_count=None):
    extra = {k[len("header.extra."):]: str(v) for k, v in values.items() if k.startswith("header.extra.")}
    header = StatementHeader(
        bank_name=values.get("header.bank_name") or (template.bank_name if template else bank_name),
        bank_address=values.get("header.bank_address"),
        account_holder_name=values.get("header.account_holder_name"),
        account_holder_address=values.get("header.account_holder_address"),
        account_number=values.get("header.account_number"),
        ifsc=values.get("header.ifsc"),
        branch=values.get("header.branch"),
        currency=values.get("header.currency"),
        period_start=values.get("header.period_start"),
        period_end=values.get("header.period_end"),
        opening_balance=values.get("header.opening_balance"),
        closing_balance=values.get("header.closing_balance"),
        extra=extra,
    )
    summary = None
    if any(k.startswith("summary.") for k in values):
        summary = StatementSummary(
            total_debits=values.get("summary.total_debits"), total_credits=values.get("summary.total_credits"),
            debit_count=values.get("summary.debit_count"), credit_count=values.get("summary.credit_count"),
        )
    provenance = Provenance(
        document_id=document_id, strategy=strategy,
        template_id=template.template_id if template else None,
        template_version=template.version if template else None,
        pdf_backend=backend, page_count=page_count,
        extracted_at=datetime.now(timezone.utc).replace(microsecond=0),
    )
    return Statement(header=header, transactions=transactions, summary=summary, provenance=provenance)


def extract_with_template(doc, template, document_id=None, strategy="template"):
    """Deterministic extraction of `doc` (an open PdfDocument) using `template`."""
    t0 = time.perf_counter()
    count = doc.page_count
    wanted = _pages_needed_by_rules(template.header_rules, count)
    kept = {}

    def pages():
        for page in doc.iter_pages():
            if page.number in wanted:
                kept[page.number] = page      # only header pages are retained
            yield page

    rows, problems = assemble_rows(pages(), template.table)
    t1 = time.perf_counter()
    txns, row_problems = rows_to_transactions(rows, template.table)
    values, header_problems = apply_header_rules(template.header_rules, kept, count, template.table.date_formats,
                                                 template.table.line_tolerance)
    statement = build_statement(values, txns, template, document_id=document_id, strategy=strategy,
                                backend=doc.backend, page_count=count)
    t2 = time.perf_counter()
    return ExtractionResult(statement, tuple(problems) + tuple(row_problems) + tuple(header_problems),
                            {"layout_and_rows": round((t1 - t0) * 1000, 1), "normalize": round((t2 - t1) * 1000, 1)})
