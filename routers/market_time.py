from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_time.calendar import ExchangeCalendarsProvider
from market_time.models import MarketClock
from market_time.normalization import TemporalNormalizationError, as_utc
from market_time.repository import SQLExchangeHoursRepository
from market_time.service import MarketTimeService

router = APIRouter()


@router.get("/exchanges/{exchange}", response_model=MarketClock)
def exchange_clock(
    exchange: str,
    at: datetime | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        observed_at = as_utc(at) if at is not None else None
    except TemporalNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with SessionLocal() as db:
        return MarketTimeService(
            SQLExchangeHoursRepository(db), ExchangeCalendarsProvider()
        ).snapshot(exchange, at=observed_at)