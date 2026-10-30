from fastapi import APIRouter, Depends, HTTPException

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from scheduler_repository import SchedulerRepository
from services.currencies import get_currency_catalog

router = APIRouter()


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
        result = SchedulerRepository(db).request_run(
            job_key="market_price_refresh_request",
            requested_by=current_user["user_id"],
            payload={},
            source="PRICE_REFRESH_UI",
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
