"""
Candidate-template proposers used by the template agent.

The agent is a bounded propose -> test -> refine loop; *how* a candidate is
proposed is pluggable:

- HeuristicProposer (default): deterministic layout inference + the generic
  header-rule library. Local, free, reproducible.
- LLMProposer: for layouts the heuristics cannot infer (e.g. no ruled cells).
  A *local* model (Ollama on this machine, never a hosted service) labels the
  heading lines and column roles; code builds and verifies the template
  (learning/llm_view.py, DECISIONS.md D-023). Off unless an LLM token budget
  and an installed model are configured.
"""
from dataclasses import replace
import hashlib

from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES, identify_bank
from bank_parser.extraction.header import apply_header_rules, resolve_pages
from bank_parser.extraction.inference import infer_layouts
from bank_parser.learning import llm_view
from bank_parser.learning.markers import MAX_MARKERS, candidate_labels, choose_markers, heading_runs
from bank_parser.llm.ollama import LLMError
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
            yield from self._unruled(ctx)
            return
        for cand in candidates:
            yield template_for(ctx, cand.spec, self.name, {"role_source": cand.role_source})

    def _unruled(self, ctx):
        """No ruled cells: find the table from word geometry and label it from familiar heading words."""
        try:
            view = llm_view.find_first_table(ctx.doc)
        except llm_view.AnswerError as e:
            ctx.notes.append(f"heuristic: {e}")
            return
        roles = llm_view.word_roles(view)
        if roles is None:
            ctx.notes.append("heuristic: heading words do not name the columns")
            return
        try:
            layout = llm_view.layout_from_roles(ctx.doc, view, *roles)
        except llm_view.AnswerError as e:
            ctx.notes.append(f"heuristic: {e}")
            return
        yield template_for(ctx, layout.spec, self.name, {"role_source": "heading_words"})

    def refine(self, ctx, template, feedback):
        return refine_markers(ctx, template, feedback) or self._refine_rows(template, feedback)

    def _refine_rows(self, template, feedback):
        if feedback == "rows_unverified" and template.table.line_tolerance == 3.0:
            return replace(template, table=replace(template.table, line_tolerance=4.5))
        return None


def template_for(ctx, spec, proposer, provenance=None):
    """A candidate Template for `spec`: generic header rules that fire here, markers, layout id."""
    pages = {n: ctx.doc.page(n) for n in set(resolve_pages((1, 2, -1), ctx.doc.page_count))}
    rules, targets = _matching_rules(GENERIC_HEADER_RULES, pages, ctx.doc.page_count, spec.date_formats)
    values, _ = apply_header_rules(rules, pages, ctx.doc.page_count, spec.date_formats)
    bank_code, bank_name = identify_bank(values.get("header.ifsc"), ctx.text)
    personal = [str(values.get(k, "")) for k in ("header.account_holder_name", "header.account_holder_address",
                                                  "header.branch", "header.bank_address")]
    markers = choose_markers(ctx.text, personal, heading_labels=heading_runs(ctx.text, spec.header_tokens))
    existing = ctx.registry_layout_for(bank_code, markers)
    layout_id = existing or f"layout-{layout_fingerprint(bank_code, markers, spec)}"
    required = ["balance_chain"] if any(c.role == "balance" for c in spec.columns) else []
    if "summary.total_debits" in targets or "header.closing_balance" in targets:
        required.append("reconciliation")
    return Template(
        bank_code=bank_code, bank_name=bank_name or "Unknown bank", layout_id=layout_id,
        version=ctx.next_version(bank_code, layout_id), markers=markers, table=spec,
        header_rules=rules, status="approved", required_checks=tuple(required),
        provenance={"created_by": "template_agent", "proposer": proposer, "created_at": now_iso(),
                    "source_document_id": ctx.document_id, **(provenance or {})},
    )


def refine_markers(ctx, template, feedback):
    """Use more (and more specific) labels so the layout stops matching other documents
    (a candidate that changes a golden statement usually took over its detection)."""
    if feedback not in ("markers_ambiguous", "golden_changed"):
        return None
    labels = [l for l in candidate_labels(ctx.text) if l not in template.markers]
    extra = tuple(l for l in labels if len(l.split()) >= 2)[: MAX_MARKERS]
    if not extra:
        return None
    return replace(template, markers=template.markers + extra)


class LLMProposer(Proposer):
    """A local model labels the columns of a table that code has found (learning/llm_view.py).

    `client` is an OllamaClient (or a test double with the same `has_model` / `chat_json`).
    Each call's tokens are charged to ctx.llm_tokens_left; no call is made once it is spent.
    """
    name = "llm"
    uses_llm = True

    def __init__(self, client=None, model=None, examples=()):
        self.client, self.model = client, model
        # Approved table_roles examples shown as few-shot. Off in the app: they made the model worse on
        # tables with little heading text (D-027); kept for experiments/examples_holdout.py.
        self.examples = list(examples)
        self._available = None

    def available(self):
        if self._available is None:
            self._available = bool(self.client and self.model and self.client.has_model(self.model))
        return self._available

    def _ask(self, ctx, view, note=""):
        if ctx.llm_tokens_left <= 0:
            ctx.notes.append("llm: token budget spent")
            return None
        from bank_parser.learning.examples import few_shot, nearest     # examples imports this module
        shown = few_shot(nearest(self.examples, view)) if self.examples else ""
        prompt = llm_view.build_prompt(view, shown) + (f"\n\n{note}" if note else "")
        ctx.llm_calls += 1
        try:
            reply = self.client.chat_json(self.model, llm_view.SYSTEM, prompt, llm_view.answer_schema(view))
        except LLMError as e:
            ctx.llm_failures += 1
            ctx.notes.append(f"llm: {e}")
            return None
        ctx.llm_retries += getattr(reply, "retries", 0)
        ctx.llm_tokens_left -= reply.tokens
        ctx.llm_tokens_used += reply.tokens
        try:
            layout = llm_view.layout_from_roles(ctx.doc, view, *llm_view.roles_from_answer(view, reply.data))
        except llm_view.AnswerError as e:
            ctx.notes.append(f"llm: {e}")
            return None
        extra = {"examples_shown": len(shown.splitlines())} if shown else {}
        return template_for(ctx, layout.spec, self.name, {"model": self.model, "role_source": "llm", **extra})

    def propose(self, ctx):
        try:
            ctx.llm_view = llm_view.find_first_table(ctx.doc)
        except llm_view.AnswerError as e:
            ctx.notes.append(f"llm: {e}")
            return
        candidate = self._ask(ctx, ctx.llm_view)
        if candidate is not None:
            yield candidate

    def refine(self, ctx, template, feedback):
        refined = refine_markers(ctx, template, feedback)
        if refined is not None or feedback != "rows_unverified":
            return refined
        # Debit and credit look alike (both amounts); the balance chain decides. Swap them once
        # before asking the model again (the heuristic does the same for unlabelled tables).
        cols = template.table.columns
        if {"debit", "credit"} <= {c.role for c in cols} and not template.provenance.get("swapped"):
            swap = {"debit": "credit", "credit": "debit"}
            table = replace(template.table, columns=tuple(replace(c, role=swap.get(c.role, c.role)) for c in cols))
            return replace(template, table=table, provenance={**template.provenance, "swapped": True})
        roles = ", ".join(c.role for c in sorted(template.table.columns, key=lambda c: c.x0))
        note = (f"Note: the labelling [{roles}] gave rows whose balances do not add up. "
                "Check debit vs credit and which column is the balance.")
        again = self._ask(ctx, ctx.llm_view, note)
        if again is None or again.table == template.table:
            return None
        return again
