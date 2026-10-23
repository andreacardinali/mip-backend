from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.currencies import normalize_supported_currency

router = APIRouter()


@router.get("/")
async def get_portfolios(
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT * FROM sp_get_portfolios(:user_id)"),
            {"user_id": current_user["user_id"]},
        )
        return [dict(row._mapping) for row in rows]
    finally:
        db.close()


@router.post("/upsert")
async def upsert_portfolio(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        base_currency = normalize_supported_currency(
            payload.get("base_currency"),
            market_data,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Currency catalog is temporarily unavailable",
        ) from error

    db = SessionLocal()
    try:
        portfolio_id = db.execute(
            text(
                """
                SELECT sp_upsert_portfolio(
                    :portfolio_id,
                    :user_id,
                    :name,
                    :portfolio_type,
                    :description,
                    :base_currency
                )
                """
            ),
            {
                "portfolio_id": payload.get("portfolio_id"),
                "user_id": current_user["user_id"],
                "name": payload["name"],
                "portfolio_type": payload.get("portfolio_type", "PORTFOLIO"),
                "description": payload.get("description"),
                "base_currency": base_currency,
            },
        ).scalar()
        db.commit()
        return {"portfolio_id": str(portfolio_id), "status": "saved"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        deleted = db.execute(
            text("SELECT sp_delete_portfolio(:portfolio_id, :user_id)"),
            {"portfolio_id": portfolio_id, "user_id": current_user["user_id"]},
        ).scalar()
        db.commit()
        return {"deleted": deleted}
    finally:
        db.close()


@router.get("/{portfolio_id}/summary")
async def get_portfolio_summary(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        parameters = {
            "portfolio_id": portfolio_id,
            "user_id": current_user["user_id"],
        }
        row = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_portfolio_summary(
                    :portfolio_id,
                    :user_id
                )
                """
            ),
            parameters,
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        positions = db.execute(
            text(
                """
                SELECT
                    p.last_price,
                    COALESCE(
                        q.status,
                        CASE
                            WHEN p.last_price IS NOT NULL THEN 'COMPLETED'
                            ELSE 'UNAVAILABLE'
                        END
                    ) AS price_refresh_status
                FROM sp_get_positions(
                    :portfolio_id,
                    :user_id
                ) p
                LEFT JOIN market_price_refresh_queue q
                    ON q.instrument_id = p.instrument_id
                """
            ),
            parameters,
        ).all()

        result = dict(row._mapping)
        pending_statuses = {"PENDING", "PROCESSING", "RETRY"}
        pending_price_count = sum(
            1
            for position in positions
            if position.last_price is None
            or str(position.price_refresh_status or "").upper() in pending_statuses
        )
        total_position_count = len(positions)
        priced_position_count = total_position_count - pending_price_count
        pricing_complete = pending_price_count == 0

        result.update(
            {
                "pricing_complete": pricing_complete,
                "pending_price_count": pending_price_count,
                "priced_position_count": priced_position_count,
                "total_position_count": total_position_count,
                "partial_portfolio_value_native": result.get("portfolio_value_native"),
                "partial_portfolio_value_reporting": result.get(
                    "portfolio_value_reporting"
                ),
            }
        )

        if not pricing_complete:
            result.update(
                {
                    "portfolio_value_native": None,
                    "profit_loss_native": None,
                    "portfolio_value_reporting": None,
                    "profit_loss_reporting": None,
                    "profit_loss_pct": None,
                }
            )

        return result
    finally:
        db.close()
