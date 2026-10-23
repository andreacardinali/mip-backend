from fastapi import APIRouter, Depends
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()
PRICE_MAX_AGE_HOURS = 96


@router.get("/{watchlist_id}/items")
def get_watchlist_items(
    watchlist_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT public.sp_get_watchlist_items_with_pricing(
                    CAST(:watchlist_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:max_age_hours AS numeric)
                ) AS payload
                """
            ),
            {
                "watchlist_id": watchlist_id,
                "user_id": current_user["user_id"],
                "max_age_hours": PRICE_MAX_AGE_HOURS,
            },
        )
        return [row.payload for row in rows]
    finally:
        db.close()


@router.post("/upsert")
def upsert_watchlist_item(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        item_id = db.execute(
            text(
                """
                SELECT sp_upsert_watchlist_item(
                    CAST(:watchlist_item_id AS uuid),
                    CAST(:watchlist_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:entity_id AS uuid),
                    CAST(:priority AS smallint),
                    CAST(:target_price AS numeric),
                    CAST(:notes AS text)
                )
                """
            ),
            {
                "watchlist_item_id": payload.get("watchlist_item_id"),
                "watchlist_id": payload["watchlist_id"],
                "user_id": current_user["user_id"],
                "entity_id": payload["entity_id"],
                "priority": payload.get("priority"),
                "target_price": payload.get("target_price"),
                "notes": payload.get("notes"),
            },
        ).scalar()
        db.commit()
        return {"watchlist_item_id": str(item_id), "status": "saved"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{watchlist_item_id}")
def delete_watchlist_item(
    watchlist_item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        deleted = db.execute(
            text(
                """
                SELECT sp_delete_watchlist_item(
                    CAST(:watchlist_item_id AS uuid),
                    CAST(:user_id AS uuid)
                )
                """
            ),
            {
                "watchlist_item_id": watchlist_item_id,
                "user_id": current_user["user_id"],
            },
        ).scalar()
        db.commit()
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
