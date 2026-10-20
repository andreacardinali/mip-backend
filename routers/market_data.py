from fastapi import APIRouter, Depends

from auth.security import CurrentUser, get_current_user
from market_data.factory import market_data

router = APIRouter()


@router.get("/capabilities")
def capabilities(current_user: CurrentUser = Depends(get_current_user)):
    return {"providers": market_data.capabilities()}
