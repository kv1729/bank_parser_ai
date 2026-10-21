"""
Confidence & autonomy (Stage 11, D-029): where the system has earned the right to work
without the owner.

Track record (11.1): for every layout (all versions of a template), each statement read with
it is, by the owner's reviews:
  matched      reviewed with nothing marked wrong or missing (or approved as golden by the
               owner), and never corrected on any earlier extraction with this layout
  corrected    some review of it marked something wrong or missing
  unreviewed   not reviewed yet (neither breaks nor extends the run)

`run` is the number of matched statements since the most recent corrected one (newest
first, skipping unreviewed). A layout has earned autonomy when run >= the threshold.
Only the owner's reviews and approvals count: the agent never vouches for itself.
Counts, layout ids and dates only; never statement content.
"""
import json

from bank_parser.storage.db import layout_of

MATCHED, CORRECTED, UNREVIEWED = "matched", "corrected", "unreviewed"


def _statements(db):
    """{layout: [(first_extraction_id, document_id, outcome)]} from extractions, reviews and owner goldens."""
    with db.connect() as c:
        extractions = c.execute("""SELECT id, document_id, template_id, created_at FROM extractions
                                   WHERE template_id IS NOT NULL ORDER BY id""").fetchall()
        reviews = c.execute("SELECT extraction_id, flags_json, missing_json FROM reviews").fetchall()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(golden_snapshots)")}
        by_owner = "AND COALESCE(approved_by, 'owner') = 'owner'" if "approved_by" in cols else ""
        goldens = {r[0] for r in c.execute(f"SELECT DISTINCT document_id FROM golden_snapshots "
                                           f"WHERE status = 'active' {by_owner}")}
    marked = {r["extraction_id"]: bool(json.loads(r["flags_json"]) or json.loads(r["missing_json"] or "[]"))
              for r in reviews}
    docs = {}                                   # (layout, document) -> [extraction rows]
    for e in extractions:
        docs.setdefault((layout_of(e["template_id"]), e["document_id"]), []).append(e)
    out = {}
    for (layout, doc_id), rows in docs.items():
        latest = rows[-1]["id"]
        if any(marked.get(r["id"]) for r in rows):
            outcome = CORRECTED
        elif marked.get(latest) is False or doc_id in goldens:
            outcome = MATCHED
        else:
            outcome = UNREVIEWED
        out.setdefault(layout, []).append((rows[0]["id"], doc_id, outcome))     # order: first extraction
    for items in out.values():
        items.sort()
    return out


def track_record(db, threshold, layout=None):
    """Per layout: statements, matched, corrected, unreviewed, run, threshold, earned (oldest first inside)."""
    records = []
    for lay, items in sorted(_statements(db).items()):
        if layout is not None and lay != layout:
            continue
        run = 0
        for _, _, outcome in reversed(items):
            if outcome == CORRECTED:
                break
            run += outcome == MATCHED
        counts = {k: sum(1 for *_, o in items if o == k) for k in (MATCHED, CORRECTED, UNREVIEWED)}
        records.append({"layout": lay, "statements": len(items), **counts, "run": run,
                        "threshold": threshold, "earned": run >= threshold})
    return records


def layout_earned(db, threshold, template_id):
    """True when the template's layout has earned autonomy."""
    lay = layout_of(template_id)
    return bool(lay) and any(r["earned"] for r in track_record(db, threshold, lay))
