from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_analysis.lifecycle_repository import SignalLifecycleRepository
from market_screener.api_repository import MarketScreenerApiRepository

router = APIRouter()


class ScreenerAnalysisRequest(BaseModel):
    context_type: Literal["WATCHLIST", "PORTFOLIO", "UNIVERSE"]
    context_id: UUID
    llm_enabled: bool = False


@router.get("/contexts")
def list_screener_contexts(current_user: CurrentUser = Depends(get_current_user)):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_contexts(
            user_id=current_user["user_id"]
        )
    finally:
        db.close()


@router.post("/analyze")
def analyze_screener(
    request: ScreenerAnalysisRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    from config.settings import settings
    from market_analysis.scan import ScanConfig
    from market_data.factory import market_data
    from market_screener.service import analyze_screener_context

    db = SessionLocal()
    try:
        result = analyze_screener_context(
            db,
            user_id=current_user["user_id"],
            context_type=request.context_type,
            context_id=str(request.context_id),
            market_data_service=market_data,
            config=ScanConfig.from_settings(settings),
            **({"llm_enabled": True, "llm_settings": settings} if request.llm_enabled else {}),
        )
        db.commit()
        return result
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from error
    except DBAPIError as error:
        db.rollback()
        code = getattr(error.orig, "pgcode", None)
        if code == "P0002":
            raise HTTPException(404, "Context not found") from error
        if code == "55P03":
            raise HTTPException(409, "Analysis already running") from error
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("")
def list_screener_opportunities(
    origin: str | None = None,
    direction: str | None = None,
    verdict: str | None = None,
    portfolio_match: bool | None = None,
    consensus_direction: str | None = None,
    strategy_code: str | None = Query(None, min_length=1, max_length=100),
    context_type: Literal["WATCHLIST", "PORTFOLIO", "UNIVERSE"] | None = None,
    context_id: UUID | None = None,
    minimum_score: float = Query(0.0, ge=0, le=100),
    query: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    if (context_type is None) != (context_id is None):
        raise HTTPException(
            422, "context_type and context_id must be supplied together"
        )
    db = SessionLocal()
    try:
        repository = MarketScreenerApiRepository(db)
        if context_type is not None:
            repository.context_instruments(
                user_id=current_user["user_id"],
                context_type=context_type,
                context_id=str(context_id),
            )
        return repository.list_opportunities(
            user_id=current_user["user_id"],
            origin=origin,
            direction=direction,
            verdict=verdict,
            portfolio_match=portfolio_match,
            consensus_direction=consensus_direction,
            strategy_code=strategy_code.strip().upper() if strategy_code else None,
            context_type=context_type,
            context_id=str(context_id) if context_id else None,
            minimum_score=minimum_score,
            query=query,
            limit=limit,
        )
    except DBAPIError as error:
        if getattr(error.orig, "pgcode", None) == "P0002":
            raise HTTPException(404, "Context not found") from error
        raise
    finally:
        db.close()


@router.get("/runs")
def list_screener_runs(
    limit: int = Query(50, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_runs(limit)
    finally:
        db.close()


@router.get("/snapshots")
def list_screener_snapshots(
    limit: int = Query(50, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_snapshots(limit)
    finally:
        db.close()


@router.get("/changes")
def get_screener_snapshot_changes(
    snapshot_id: UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).get_snapshot_changes(
            str(snapshot_id) if snapshot_id else None
        )
    finally:
        db.close()


@router.get("/snapshots/{snapshot_id}/items")
def list_screener_snapshot_items(
    snapshot_id: str, current_user: CurrentUser = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_snapshot_items(snapshot_id)
    finally:
        db.close()


@router.get("/{opportunity_id}/signal-events")
def list_screener_signal_events(
    opportunity_id: UUID,
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        opportunity = MarketScreenerApiRepository(db).get_opportunity(
            user_id=current_user["user_id"], opportunity_id=str(opportunity_id)
        )
        if opportunity is None:
            raise HTTPException(404, "Opportunity not found")
        return SignalLifecycleRepository(db).list_events(
            instrument_id=opportunity["source_instrument_id"], limit=limit
        )
    finally:
        db.close()


@router.get("/performance")
def list_screener_signal_performance(
    instrument_id: UUID | None = None,
    strategy_code: str | None = Query(None, min_length=1, max_length=100),
    status: Literal["OPEN", "CLOSED"] | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return SignalLifecycleRepository(db).list_performance(
            instrument_id=str(instrument_id) if instrument_id else None,
            strategy_code=strategy_code.strip().upper() if strategy_code else None,
            status=status,
            limit=limit,
            offset=offset,
        )
    finally:
        db.close()


@router.get("/{opportunity_id}")
def get_screener_opportunity(
    opportunity_id: str, current_user: CurrentUser = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        payload = MarketScreenerApiRepository(db).get_opportunity(
            user_id=current_user["user_id"], opportunity_id=opportunity_id
        )
        if payload is None:
            raise HTTPException(404, "Opportunity not found")
        return payload
    finally:
        db.close()
