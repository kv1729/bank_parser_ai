"""
Candidate-template proposers used by the template agent.

The agent is a bounded propose -> test -> refine loop; *how* a candidate is
proposed is pluggable:

- HeuristicProposer (default): deterministic layout inference + the generic
  header-rule library. Local, free, reproducible.
- LLMProposer: reserved for layouts the heuristics cannot infer. Disabled
  unless explicitly configured, and then sees only a redacted layout summary,
  never the statement (DECISIONS.md D-011). Not implemented yet: no layout
  has needed it.
"""
from dataclasses import replace
import hashlib

from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES, identify_bank
from bank_parser.extraction.header import apply_header_rules, resolve_pages
from bank_parser.extraction.inference import infer_layouts
from bank_parser.learning.markers import MAX_MARKERS, candidate_labels, choose_markers
from bank_parser.templates.model import Template, now_iso


class Proposer:
    name = "abstract"
    uses_llm = False

    def available(self):
        return True

    def propose(self, ctx):
        """Yield candidate Templates for the document in `ctx`."""
        raise NotImplementedError

    def refine(self, ctx, template, feedback):
        """Return an adjusted Template for one bounded retry, or None."""
        return None


def _matching_rules(rules, pages, page_count, date_formats):
    """Generic rules that actually produce a value on this document, in order."""
    kept, seen = [], set()
    for rule in rules:
        values, _ = apply_header_rules((rule,), pages, page_count, date_formats)
        new = [t for t in values if t not in seen]
        if new:
            kept.append(rule)
            seen.update(values)
    return tuple(kept), seen


def layout_fingerprint(bank_code, markers, spec):
    basis = "|".join([bank_code, ";".join(sorted(markers)),
                      ";".join(f"{c.role}:{round(c.x0)}-{round(c.x1)}" for c in spec.columns)])
    return hashlib.sha256(basis.encode()).hexdigest()[:8]


class HeuristicProposer(Proposer):
    name = "heuristic"

    def propose(self, ctx):
        candidates = infer_layouts(ctx.cells_doc, ctx.doc)
        if not candidates:
            ctx.notes.append("heuristic: no ruled transaction table found")
            return
        pages = {n: ctx.doc.page(n) for n in
                 set(resolve_pages((1, 2, -1), ctx.doc.page_count))}
        for cand in candidates:
            rules, targets = _matching_rules(GENERIC_HEADER_RULES, pages, ctx.doc.page_count, cand.spec.date_formats)
            values, _ = apply_header_rules(rules, pages, ctx.doc.page_count, cand.spec.date_formats)
            bank_code, bank_name = identify_bank(values.get("header.ifsc"), ctx.text)
            personal = [str(values.get(k, "")) for k in ("header.account_holder_name", "header.account_holder_address",
                                                          "header.branch", "header.bank_address")]
            markers = choose_markers(ctx.text, personal)
            existing = ctx.registry_layout_for(bank_code, markers)
            layout_id = existing or f"layout-{layout_fingerprint(bank_code, markers, cand.spec)}"
            required = ["balance_chain"] if any(c.role == "balance" for c in cand.spec.columns) else []
            if "summary.total_debits" in targets or "header.closing_balance" in targets:
                required.append("reconciliation")
            yield Template(
                bank_code=bank_code, bank_name=bank_name or "Unknown bank", layout_id=layout_id,
                version=ctx.next_version(bank_code, layout_id), markers=markers, table=cand.spec,
                header_rules=rules, status="approved", required_checks=tuple(required),
                provenance={"created_by": "template_agent", "proposer": self.name, "created_at": now_iso(),
                            "source_document_id": ctx.document_id, "role_source": cand.role_source},
            )

    def refine(self, ctx, template, feedback):
        if feedback == "markers_ambiguous":
            # Use more (and more specific) labels so the layout stops matching other documents.
            personal = ()
            labels = [l for l in candidate_labels(ctx.text) if l not in template.markers]
            extra = tuple(l for l in labels if len(l.split()) >= 2)[: MAX_MARKERS]
            if not extra:
                return None
            return replace(template, markers=template.markers + extra)
        if feedback == "rows_unverified" and template.table.line_tolerance == 3.0:
            return replace(template, table=replace(template.table, line_tolerance=4.5))
        return None


class LLMProposer(Proposer):
    """Placeholder for LLM-assisted layout proposals (disabled; see module docstring)."""
    name = "llm"
    uses_llm = True

    def __init__(self, enabled=False):
        self.enabled = enabled

    def available(self):
        return False   # not implemented: no layout so far has defeated the heuristic proposer

    def propose(self, ctx):
        return iter(())
