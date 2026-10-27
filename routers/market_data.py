from fastapi import APIRouter, Depends, HTTPException

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.currencies import get_currency_catalog
from services.market_price_refresh import (
    enqueue_user_price_refreshes,
)

router = APIRouter()
LIVE_PRICE_MAX_AGE_SECONDS = 30


@router.get("/capabilities")
def capabilities(current_user: CurrentUser = Depends(get_current_user)):
    return {"providers": market_data.capabilities()}


@router.get("/currencies")
def currencies(current_user: CurrentUser = Depends(get_current_user)):
    try:
        return get_currency_catalog(market_data).to_dict()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Currency catalog is temporarily unavailable",
        ) from error


@router.post("/prices/refresh", status_code=202)
def refresh_user_prices(
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        enqueue_result = enqueue_user_price_refreshes(
            db,
            user_id=current_user["user_id"],
            max_age_seconds=LIVE_PRICE_MAX_AGE_SECONDS,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "status": "QUEUED",
        "max_age_seconds": LIVE_PRICE_MAX_AGE_SECONDS,
        **enqueue_result,
    }
