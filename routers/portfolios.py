from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.currencies import normalize_supported_currency

router = APIRouter()


def _get_pricing_state(db, portfolio_id, user_id):
    positions = db.execute(
        text(
            """
            SELECT p.last_price
            FROM sp_get_positions(:portfolio_id, :user_id) p
            """
        ),
        {"portfolio_id": portfolio_id, "user_id": user_id},
    ).all()
    pending_price_count = sum(
        1 for position in positions if position.last_price is None
    )
    total_position_count = len(positions)
    return {
        "pricing_complete": pending_price_count == 0,
        "pending_price_count": pending_price_count,
        "priced_position_count": total_position_count - pending_price_count,
        "total_position_count": total_position_count,
    }


def _apply_pricing_state(result, pricing_state, *, list_view=False):
    result.update(pricing_state)
    if list_view:
        result["partial_portfolio_value"] = result.get("portfolio_value")
    else:
        result["partial_portfolio_value_native"] = result.get("portfolio_value_native")
        result["partial_portfolio_value_reporting"] = result.get(
            "portfolio_value_reporting"
        )
    if pricing_state["pricing_complete"]:
        return result
    if list_view:
        result.update(
            {
                "portfolio_value": None,
                "profit_loss": None,
                "profit_loss_pct": None,
            }
        )
    else:
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


@router.get("/")
async def get_portfolios(current_user: CurrentUser = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user_id = current_user["user_id"]
        rows = db.execute(
            text("SELECT * FROM sp_get_portfolios(:user_id)"),
            {"user_id": user_id},
        )
        results = []
        for row in rows:
            result = dict(row._mapping)
            pricing_state = _get_pricing_state(db, result["portfolio_id"], user_id)
            results.append(_apply_pricing_state(result, pricing_state, list_view=True))
        return results
    finally:
        db.close()


@router.post("/upsert")
async def upsert_portfolio(
    payload: dict, current_user: CurrentUser = Depends(get_current_user)
):
    try:
        base_currency = normalize_supported_currency(
            payload.get("base_currency"), market_data
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="Currency catalog is temporarily unavailable"
        ) from error
    db = SessionLocal()
    try:
        portfolio_id = db.execute(
            text(
                """
                SELECT sp_upsert_portfolio(
                    :portfolio_id, :user_id, :name, :portfolio_type,
                    :description, :base_currency
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
    portfolio_id: str, current_user: CurrentUser = Depends(get_current_user)
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
    portfolio_id: str, current_user: CurrentUser = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        user_id = current_user["user_id"]
        parameters = {"portfolio_id": portfolio_id, "user_id": user_id}
        row = db.execute(
            text("SELECT * FROM sp_get_portfolio_summary(:portfolio_id, :user_id)"),
            parameters,
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        result = dict(row._mapping)
        pricing_state = _get_pricing_state(db, portfolio_id, user_id)
        return _apply_pricing_state(result, pricing_state)
    finally:
        db.close()
