from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


@router.get("/portfolio/{portfolio_id}")
def get_positions(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    p.*,
                    COALESCE(
                        q.status,
                        CASE
                            WHEN p.last_price IS NOT NULL THEN 'COMPLETED'
                            ELSE 'UNAVAILABLE'
                        END
                    ) AS price_refresh_status,
                    q.attempt_count AS price_refresh_attempt_count,
                    q.max_attempts AS price_refresh_max_attempts,
                    q.next_attempt_at AS price_refresh_next_attempt_at,
                    q.last_error AS price_refresh_error
                FROM sp_get_positions(
                    :portfolio_id,
                    :user_id
                ) p
                LEFT JOIN market_price_refresh_queue q
                    ON q.instrument_id = p.instrument_id
                """
            ),
            {"portfolio_id": portfolio_id, "user_id": user_id},
        )
        return [dict(row._mapping) for row in rows]
    finally:
        db.close()


@router.post("/upsert")
def upsert_position(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    if "instrument_id" not in payload:
        raise HTTPException(status_code=422, detail="instrument_id is required")

    db = SessionLocal()
    try:
        position_id = db.execute(
            text(
                """
                SELECT sp_upsert_position(
                    CAST(:position_id AS uuid),
                    CAST(:portfolio_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:instrument_id AS uuid),
                    CAST(:quantity AS numeric),
                    CAST(:avg_price AS numeric),
                    CAST(:opened_at AS timestamptz),
                    CAST(:notes AS text)
                )
                """
            ),
            {
                "position_id": payload.get("position_id"),
                "portfolio_id": payload["portfolio_id"],
                "user_id": user_id,
                "instrument_id": payload["instrument_id"],
                "quantity": payload["quantity"],
                "avg_price": payload.get("avg_price"),
                "opened_at": payload.get("opened_at"),
                "notes": payload.get("notes"),
            },
        ).scalar()
        db.commit()
        return {"position_id": str(position_id), "status": "saved"}
    except IntegrityError as error:
        db.rollback()
        diagnostic = getattr(error.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == "uq_position":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "POSITION_ALREADY_EXISTS",
                    "message": "This instrument already exists in the portfolio.",
                },
            ) from error
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{position_id}")
def delete_position(
    position_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()
    try:
        deleted = db.execute(
            text("SELECT sp_delete_position(:position_id, :user_id)"),
            {"position_id": position_id, "user_id": user_id},
        ).scalar()
        db.commit()
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
