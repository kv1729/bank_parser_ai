"""
The owner's approvals and corrections as training examples (Stage 10.7, D-027).

Recorded (storage table `training_examples`, append-only, never statement values):
  approval          a golden approval of a statement read with a template
  table_roles       its table's heading words per column and the approved roles
  field_correction  account/summary fields the owner marked wrong or missing, and
                    how many rows were marked wrong
  verdict           the owner's Agree/Disagree on one self-review item (the note
                    stays in the review and is never used here)

Used, deterministically:
  - ExamplesProposer: a new table whose headings were all seen in approved
    examples is labelled from them, with no model call;
  - few_shot(): the nearest approved examples are shown to the local model as
    "heading -> role" lines (headings only, never values);
  - self_review.apply_history(): fields the owner corrected on a layout are not
    rated "high" again until the layout is approved again.
"""
import logging
import re

from bank_parser.extraction.inference import _label_roles
from bank_parser.learning import llm_view
from bank_parser.learning.proposers import Proposer, refine_markers, template_for
from bank_parser.self_review import item_kind

log = logging.getLogger("bank_parser")
MAX_HEADING_WORDS = 5
FEW_SHOT = 3


def normalise_heading(text):
    """Heading words in a comparable form; "" for anything that does not look like a heading
    (digits or many words: a value or a sentence printed above the table, never stored)."""
    # Distinct words in order: a heading printed over several lines can repeat ("Date Date").
    words = dict.fromkeys(w.strip(".") for w in re.sub(r"[^a-z0-9/.]+", " ", (text or "").lower()).split())
    t = " ".join(w for w in words if w)
    if not t or re.search(r"\d", t) or len(t.split()) > MAX_HEADING_WORDS:
        return ""
    return t


def table_example(doc, template):
    """{"headings", "roles", "amount_mode"} for the document's first table, with the roles of the
    approved template's columns (matched by overlap), or None when they cannot be matched."""
    try:
        view = llm_view.find_first_table(doc)
    except llm_view.AnswerError:
        return None
    cols = template.table.columns
    roles = []
    for k in range(len(view.headings)):
        left, right = view.bounds[k], view.bounds[k + 1]
        overlap, role = max(((min(right, c.x1) - max(left, c.x0), c.role) for c in cols), default=(0, "ignore"))
        roles.append(role if overlap > 0 else "ignore")
    named = [r for r in roles if r != "ignore"]
    if len(named) != len(set(named)) or {c.role for c in cols if c.role != "ignore"} != set(named):
        return None          # the found table and the template disagree about the columns
    return {"headings": [normalise_heading(h) for h in view.headings], "roles": roles,
            "amount_mode": template.table.amount_mode}


# --- recording -----------------------------------------------------------------------------

def record_golden(db, document_id, golden, doc=None, template=None):
    """After a golden approval: an `approval` example, and `table_roles` when the table can be
    matched. Returns the number of new examples. Never raises: an approval never fails here."""
    added = 0
    try:
        template_id, at = golden.get("template_id"), golden.get("approved_at")
        added += db.add_example("approval", document_id, template_id, "golden", {"golden_version": golden["version"]}, at)
        if doc is not None and template is not None:
            payload = table_example(doc, template)
            if payload is not None:
                added += db.add_example("table_roles", document_id, template_id, "golden", payload, at)
    except Exception as e:                  # never content: only the error type
        log.warning("training example not recorded for a golden approval (%s)", type(e).__name__)
    return added


def record_golden_file(db, path, golden, template, passwords=(), backend="pypdfium2"):
    """record_golden for a stored PDF: opens it (no password, then each candidate) to match the
    table. When it cannot be opened, only the approval is recorded. Never raises."""
    from bank_parser.golden import reopen
    from bank_parser.pdf import open_pdf
    if template is not None:
        try:
            _, password = reopen(path, list(passwords))
            with open_pdf(path, password, backend) as doc:
                return record_golden(db, golden["document_id"], golden, doc, template)
        except Exception as e:                 # no password opens it, or the PDF is gone
            log.warning("table example not recorded for a golden approval (%s)", type(e).__name__)
    return record_golden(db, golden["document_id"], golden)


def record_review(db, document_id, extraction_id, template_id, flags, missing, feedback, self_review, at=None):
    """After a review save: a `field_correction` when something is marked, and one `verdict` per
    Agree/Disagree. `at`: when the review was saved. Returns the number of new examples. Never raises."""
    added = 0
    try:
        fields_ = sorted(k for k in flags if not k.startswith("txn."))
        rows = sum(1 for k in flags if k.startswith("txn."))
        if fields_ or missing or rows:
            added += db.add_example("field_correction", document_id, template_id, "review",
                                    {"extraction_id": extraction_id, "wrong": fields_, "missing": sorted(missing),
                                     "rows_marked": rows}, at)
        items = {i["id"]: i for i in (self_review or {}).get("unsure", [])}
        for item_id, v in sorted((feedback or {}).items()):
            item = items.get(item_id, {})
            added += db.add_example("verdict", document_id, template_id, "review",
                                    {"extraction_id": extraction_id, "item": item_kind(item_id),
                                     "field": item.get("field"), "level": item.get("level"), "verdict": v["verdict"]}, at)
    except Exception as e:
        log.warning("training example not recorded for a review (%s)", type(e).__name__)
    return added


# --- using them ----------------------------------------------------------------------------

def vocabulary(examples):
    """{normalised heading: role} for headings that always had the same role in approved examples."""
    seen = {}
    for e in examples:
        p = e["payload"] if "payload" in e else e
        for h, r in zip(p["headings"], p["roles"]):
            if h:
                seen.setdefault(h, set()).add(r)
    return {h: next(iter(rs)) for h, rs in seen.items() if len(rs) == 1}


def vocabulary_roles(view, vocab):
    """(roles, amount_mode) from the learned vocabulary, falling back to the familiar heading
    words for a column it does not know; None unless the vocabulary named at least one column
    the familiar words could not, and the roles describe a table."""
    heads = [normalise_heading(h) for h in view.headings]
    words = [r or "ignore" for r in _label_roles(view.headings)]
    roles = [vocab.get(h, w) if h else w for h, w in zip(heads, words)]
    if roles == words or not any(h in vocab for h in heads if h):
        return None                    # nothing learned here: the heuristic proposer already tried this
    named = [r for r in roles if r != "ignore"]
    if len(named) != len(set(named)) or not {"txn_date", "description", "balance"} <= set(roles):
        return None
    return roles, llm_view.amount_mode(roles)


def _words(headings):
    return {w for h in headings for w in normalise_heading(h).split()}


def nearest(examples, view, k=FEW_SHOT):
    """The k approved examples whose heading words overlap the view's most (ties: oldest first)."""
    mine = _words(view.headings)
    scored = []
    for i, e in enumerate(examples):
        theirs = _words(e["payload"]["headings"])
        if mine and theirs:
            score = len(mine & theirs) / len(mine | theirs)
            if score > 0:
                scored.append((-score, i, e))
    return [e for _, _, e in sorted(scored, key=lambda s: (s[0], s[1]))[:k]]


def few_shot(examples):
    """Approved examples as prompt lines: headings and roles only, never values."""
    lines = []
    for n, e in enumerate(examples, 1):
        pairs = "; ".join(f'"{h}" -> {r}' for h, r in zip(e["payload"]["headings"], e["payload"]["roles"]) if h)
        if pairs:
            lines.append(f"Example {n}: {pairs}")
    return "\n".join(lines)


class ExamplesProposer(Proposer):
    """Labels a table from the headings of tables the owner approved (no model call)."""
    name = "examples"

    def __init__(self, examples=()):
        self.vocab = vocabulary(examples)
        self.count = len(examples)

    def available(self):
        return bool(self.vocab)

    def propose(self, ctx):
        try:
            view = llm_view.find_first_table(ctx.doc)
        except llm_view.AnswerError as e:
            ctx.notes.append(f"examples: {e}")
            return
        roles = vocabulary_roles(view, self.vocab)
        if roles is None:
            ctx.notes.append("examples: no approved example names these columns")
            return
        try:
            layout = llm_view.layout_from_roles(ctx.doc, view, *roles)
        except llm_view.AnswerError as e:
            ctx.notes.append(f"examples: {e}")
            return
        yield template_for(ctx, layout.spec, self.name, {"role_source": "examples", "examples": self.count})

    def refine(self, ctx, template, feedback):
        return refine_markers(ctx, template, feedback)
