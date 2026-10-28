from sqlalchemy import text

from db import SessionLocal


def run() -> dict:
    db = SessionLocal()
    try:
        entity_ids = (
            db.execute(text("SELECT entity_id FROM public.sp_list_orphan_entities()"))
            .scalars()
            .all()
        )
        result = {
            "status": "SUCCESS",
            "candidates": len(entity_ids),
            "deleted": 0,
            "referenced": 0,
            "skipped": 0,
        }
        for entity_id in entity_ids:
            outcome = db.execute(
                text("SELECT public.sp_cleanup_orphan_entity(:entity_id)"),
                {"entity_id": entity_id},
            ).scalar_one()
            db.commit()
            result[outcome] += 1
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
