from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_screener.api_repository import MarketScreenerApiRepository

router = APIRouter()


@router.get("")
def list_screener_opportunities(
    algorithm_version: str | None = None,
    origin: str | None = None,
    direction: str | None = None,
    verdict: str | None = None,
    portfolio_match: bool | None = None,
    consensus_direction: str | None = None,
    minimum_score: float = Query(0.0, ge=0, le=100),
    query: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_opportunities(
            user_id=current_user["user_id"],
            algorithm_version=algorithm_version,
            origin=origin,
            direction=direction,
            verdict=verdict,
            portfolio_match=portfolio_match,
            consensus_direction=consensus_direction,
            minimum_score=minimum_score,
            query=query,
            limit=limit,
        )
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


@router.get("/snapshots/{snapshot_id}/items")
def list_screener_snapshot_items(
    snapshot_id: str, current_user: CurrentUser = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        return MarketScreenerApiRepository(db).list_snapshot_items(snapshot_id)
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
