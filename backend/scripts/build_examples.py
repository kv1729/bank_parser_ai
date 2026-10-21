"""
Record training examples (Stage 10.7, D-027) for approvals and reviews made before examples
existed. Safe to run again: an example already recorded is not added twice.

Prints counts only, never statement content.

    uv run python scripts/build_examples.py
"""
from bank_parser.config import Settings
from bank_parser.golden import password_candidates
from bank_parser.learning.examples import record_golden_file, record_review
from bank_parser.pipeline import build_registry
from bank_parser.self_review import build_self_review
from bank_parser.storage import Database


def main():
    settings = Settings.from_env()
    db = Database(settings.db_path)
    registry = build_registry(settings)
    passwords = list(password_candidates(settings))
    goldens = db.active_goldens()
    added_golden = 0
    for g in goldens:
        template = registry.get(g["template_id"]) if g.get("template_id") else None
        added_golden += record_golden_file(db, settings.documents_dir / f"{g['document_id']}.pdf", g, template,
                                           passwords, settings.pdf_backend)
    with db.connect() as c:
        reviews = [(r["document_id"], r["extraction_id"]) for r in c.execute("SELECT document_id, extraction_id FROM reviews")]
    added_review = 0
    for doc_id, ex_id in reviews:
        review, ex = db.get_review(doc_id, ex_id), db.get_extraction(ex_id)
        if not review or not ex:
            continue
        sr = ex.get("self_review") or build_self_review(ex["statement"], ex["report"])
        added_review += record_review(db, doc_id, ex_id, ex.get("template_id"), review["flags"], review["missing"],
                                      review["agent_feedback"], sr, review["updated_at"])
    print(f"goldens {len(goldens)}: {added_golden} new example(s); reviews {len(reviews)}: {added_review} new example(s)")
    print("examples by kind:", db.example_counts())


if __name__ == "__main__":
    main()
