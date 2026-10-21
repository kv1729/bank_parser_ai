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


# --- 11.3 routing: who checks this statement ---------------------------------------------------

def route(confidence, verified, template_id, record):
    """Whether the owner must review an extraction, and why (codes and counts only).
    Not required only when it is verified, its confidence is high and its layout has earned
    autonomy; everything else goes to the owner, as before Stage 11."""
    level = (confidence or {}).get("level")
    if not verified:
        reason = "not_verified"
    elif level != "high":
        reason = f"confidence_{level or 'unknown'}"
    elif not template_id or record is None:
        reason = "no_layout"
    elif not record["earned"]:
        reason = "layout_not_earned"
    else:
        reason = "earned_and_high"
    out = {"required": reason != "earned_and_high", "reason": reason, "confidence": level}
    if record is not None:
        out.update(layout=record["layout"], run=record["run"], threshold=record["threshold"])
    return out


def route_extraction(db, threshold, template_id, confidence, verified):
    lay = layout_of(template_id)
    record = next(iter(track_record(db, threshold, lay)), None) if lay else None
    if lay and record is None:        # the layout's first statement: nothing on record yet
        record = {"layout": lay, "run": 0, "threshold": threshold, "earned": threshold <= 0}
    return route(confidence, verified, template_id, record)


# --- 11.5 layout drift ---------------------------------------------------------------------------

def detect_drift(text, templates, ifsc=None):
    """For a statement no template could read: (bank_code, layout, reason) when it comes from a bank
    we already have templates for, else None. `template_failed`: a known layout's markers match but
    its reading fails validation (the layout changed inside); `new_layout_for_known_bank`: no known
    layout's markers match (the bank prints a new layout)."""
    from bank_parser.extraction.detection import detect
    from bank_parser.extraction.generic_rules import identify_bank
    matched = detect(text, templates)
    if matched:
        t = matched[0].template
        return t.bank_code, layout_of(t.template_id), "template_failed"
    bank_code, _ = identify_bank(ifsc, text)
    if bank_code != "unknown" and any(t.bank_code == bank_code for t in templates):
        return bank_code, None, "new_layout_for_known_bank"
    return None


# --- 11.6 dashboard --------------------------------------------------------------------------------

def dashboard(db, threshold, templates, weeks=8):
    """Everything the autonomy page shows: counts, codes, layout ids and dates only."""
    records = {r["layout"]: r for r in track_record(db, threshold)}
    versions = {}
    for t in templates:
        versions.setdefault(layout_of(t.template_id), set()).add(t.version)
    with db.connect() as c:
        goldens = c.execute("""SELECT g.template_id, COALESCE(g.approved_by, 'owner') AS by FROM golden_snapshots g
                               WHERE g.status = 'active' AND g.version = (SELECT MAX(version) FROM golden_snapshots
                               WHERE document_id = g.document_id AND status = 'active')""").fetchall()
        latest = c.execute("""SELECT e.id, e.document_id, e.template_id, e.verified, e.routing_json FROM extractions e
                              WHERE e.id = (SELECT MAX(id) FROM extractions WHERE document_id = e.document_id)""").fetchall()
        reviewed = {r[0] for r in c.execute("SELECT extraction_id FROM reviews")}
        corrections = c.execute("""SELECT strftime('%Y-W%W', updated_at) AS week, COUNT(*) FROM reviews
                                   WHERE flags_json != '[]' OR missing_json != '[]'
                                   GROUP BY week ORDER BY week DESC LIMIT ?""", (weeks,)).fetchall()
        reviews_by_week = dict(c.execute("""SELECT strftime('%Y-W%W', updated_at) AS week, COUNT(*) FROM reviews
                                            GROUP BY week""").fetchall())
    per = {}
    for g in goldens:
        d = per.setdefault(layout_of(g["template_id"]) or "(no layout)", {"owner": 0, "agent": 0})
        d[g["by"]] = d.get(g["by"], 0) + 1
    passing, waiting = {}, []
    for e in latest:
        lay = layout_of(e["template_id"]) or "(not learned)"
        p = passing.setdefault(lay, [0, 0])
        p[0] += bool(e["verified"])
        p[1] += 1
        routing = json.loads(e["routing_json"]) if e["routing_json"] else None
        if (routing is None or routing.get("required")) and e["id"] not in reviewed:
            waiting.append({"document": e["document_id"][:12], "layout": lay,
                            "reason": (routing or {}).get("reason", "before_routing")})
    layouts = []
    for lay in sorted(set(records) | set(versions) | set(per) | set(passing)):
        r = records.get(lay) or {"statements": 0, "matched": 0, "corrected": 0, "unreviewed": 0, "run": 0,
                                 "threshold": threshold, "earned": False}
        ok, total = passing.get(lay, [0, 0])
        layouts.append({"layout": lay, "versions": len(versions.get(lay, ())),
                        "golden_by_owner": per.get(lay, {}).get("owner", 0),
                        "golden_by_agent": per.get(lay, {}).get("agent", 0),
                        "verified": ok, "statements_now": total,
                        **{k: r[k] for k in ("statements", "matched", "corrected", "unreviewed", "run", "earned")}})
    return {"threshold": threshold, "layouts": layouts,
            "interventions": [{"week": w, "corrected": n, "reviews": reviews_by_week.get(w, 0)} for w, n in corrections],
            "drift": db.drift_events(limit=20), "waiting": waiting}
