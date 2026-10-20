from fastapi import APIRouter, Depends
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


@router.get("/{watchlist_id}/items")
def get_watchlist_items(
    watchlist_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    w.*,
                    COALESCE(
                        q.status,
                        CASE
                            WHEN w.last_price IS NOT NULL THEN 'COMPLETED'
                            ELSE 'UNAVAILABLE'
                        END
                    ) AS price_refresh_status,
                    q.attempt_count AS price_refresh_attempt_count,
                    q.max_attempts AS price_refresh_max_attempts,
                    q.next_attempt_at AS price_refresh_next_attempt_at,
                    q.last_error AS price_refresh_error
                FROM sp_get_watchlist_items(:watchlist_id, :user_id) w
                LEFT JOIN instruments i
                    ON i.entity_id = w.entity_id
                   AND i.symbol = w.symbol
                LEFT JOIN market_price_refresh_queue q
                    ON q.instrument_id = i.instrument_id
                """
            ),
            {"watchlist_id": watchlist_id, "user_id": user_id},
        )
        return [dict(row._mapping) for row in rows]
    finally:
        db.close()


@router.post("/upsert")
def upsert_watchlist_item(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()
    try:
        item_id = db.execute(
            text(
                """
                SELECT sp_upsert_watchlist_item(
                    :watchlist_item_id,
                    :watchlist_id,
                    :user_id,
                    :entity_id,
                    :priority,
                    :target_price,
                    :notes
                )
                """
            ),
            {
                "watchlist_item_id": payload.get("watchlist_item_id"),
                "watchlist_id": payload["watchlist_id"],
                "user_id": user_id,
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
    user_id = current_user["user_id"]
    db = SessionLocal()
    try:
        deleted = db.execute(
            text("SELECT sp_delete_watchlist_item(:watchlist_item_id, :user_id)"),
            {"watchlist_item_id": watchlist_item_id, "user_id": user_id},
        ).scalar()
        db.commit()
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
