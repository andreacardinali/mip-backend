from fastapi import APIRouter, Depends, HTTPException

from auth.security import CurrentUser, get_current_user
from market_data.factory import market_data
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
