from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import settings
from market_time.calendar import ExchangeCalendarsProvider
from market_time.models import MarketStatus
from market_time.repository import SQLExchangeHoursRepository
from market_time.service import MarketTimeService


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _opportunity_is_fresh(
    payload: dict[str, Any],
    *,
    market_time: MarketTimeService | None = None,
    session_open_cache: dict[str, datetime | None] | None = None,
) -> bool:
    try:
        price_max_age = timedelta(
            hours=int(settings.get("MARKET_SCREENER_MAX_PRICE_AGE_HOURS", 72))
        )
        analysis_max_age = timedelta(
            hours=int(settings.get("MARKET_SCREENER_MAX_ANALYSIS_AGE_HOURS", 72))
        )
        if price_max_age.total_seconds() <= 0 or analysis_max_age.total_seconds() <= 0:
            return False
        last_price = float(payload.get("last_price"))
        if not isfinite(last_price) or last_price <= 0:
            return False
        now = datetime.now(UTC)
        price_as_of = _parse_timestamp(payload.get("price_as_of"))
        last_analysis_at = _parse_timestamp(payload.get("last_analysis_at"))
        if price_as_of is None or last_analysis_at is None:
            return False
        price_age = now - price_as_of
        analysis_age = now - last_analysis_at
        return (
            timedelta(0) <= price_age <= price_max_age
            and timedelta(0) <= analysis_age <= analysis_max_age
            and _price_is_current_for_market(
                payload,
                price_as_of,
                market_time=market_time,
                session_open_cache=session_open_cache,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _price_is_current_for_market(
    payload: dict[str, Any],
    price_as_of: datetime,
    *,
    market_time: MarketTimeService | None,
    session_open_cache: dict[str, datetime | None] | None,
) -> bool:
    exchange = payload.get("source_exchange")
    if not isinstance(exchange, str) or not exchange.strip() or market_time is None:
        return False
    exchange = exchange.strip()
    cache = session_open_cache if session_open_cache is not None else {}
    if exchange not in cache:
        clock = market_time.snapshot(exchange)
        if clock.status == MarketStatus.UNKNOWN:
            cache[exchange] = None
        else:
            session = clock.session or clock.previous_session
            cache[exchange] = session.opens_at if session is not None else None
    latest_session_open = cache[exchange]
    return latest_session_open is not None and price_as_of >= latest_session_open


class MarketScreenerApiRepository:
    def list_contexts(self, *, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT * FROM public.sp_market_screener_list_contexts(CAST(:user_id AS uuid))"
            ),
            {"user_id": user_id},
        ).mappings()
        return [dict(row) for row in rows]

    def prepare_context_scan(
        self, *, user_id: str, context_type: str, context_id: str
    ) -> str:
        return str(
            self.db.execute(
                text(
                    "SELECT public.sp_market_screener_prepare_context_scan("
                    "CAST(:user_id AS uuid), CAST(:context_type AS varchar), CAST(:context_id AS uuid))"
                ),
                {
                    "user_id": user_id,
                    "context_type": context_type,
                    "context_id": context_id,
                },
            ).scalar_one()
        )

    def __init__(self, db: Session):
        self.db = db
        self.market_time = MarketTimeService(
            SQLExchangeHoursRepository(db), ExchangeCalendarsProvider()
        )

    def context_instruments(
        self, *, user_id: str, context_type: str, context_id: str
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT * FROM public.sp_market_screener_context_instruments("
                "CAST(:user_id AS uuid), CAST(:context_type AS varchar), "
                "CAST(:context_id AS uuid))"
            ),
            {
                "user_id": user_id,
                "context_type": context_type,
                "context_id": context_id,
            },
        ).mappings()
        return [dict(row) for row in rows]

    def list_opportunities(
        self,
        *,
        user_id: str,
        origin: str | None = None,
        direction: str | None = None,
        verdict: str | None = None,
        portfolio_match: bool | None = None,
        watchlist_match: bool | None = None,
        consensus_direction: str | None = None,
        strategy_code: str | None = None,
        context_type: str | None = None,
        context_id: str | None = None,
        minimum_score: float = 0.0,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        watchlist_argument = (
            ",CAST(:watchlist_match AS boolean)" if watchlist_match is not None else ""
        )
        rows = self.db.execute(
            text(
                """SELECT payload
            FROM public.sp_market_screener_list_filtered_opportunities(
            CAST(:user_id AS uuid),CAST(:origin AS varchar),CAST(:direction AS varchar),
            CAST(:verdict AS varchar),CAST(:portfolio_match AS boolean),CAST(:minimum_score AS numeric),
            CAST(:query AS text),CAST(:consensus_direction AS varchar),
            CAST(:strategy_code AS varchar),CAST(:context_type AS varchar),
            CAST(:context_id AS uuid),CAST(:limit AS integer)"""
                + watchlist_argument
                + ") payload"
            ),
            {
                "user_id": user_id,
                "origin": origin,
                "direction": direction,
                "verdict": verdict,
                "portfolio_match": portfolio_match,
                "watchlist_match": watchlist_match,
                "consensus_direction": consensus_direction,
                "strategy_code": strategy_code,
                "context_type": context_type,
                "context_id": context_id,
                "minimum_score": minimum_score,
                "query": query,
                "limit": max(limit, 1000),
            },
        ).scalars()
        session_open_cache: dict[str, datetime | None] = {}
        fresh = [
            dict(row)
            for row in rows
            if _opportunity_is_fresh(
                dict(row),
                market_time=self.market_time,
                session_open_cache=session_open_cache,
            )
        ]
        return fresh[:limit]

    def get_opportunity(self, *, user_id: str, opportunity_id: str):
        p = self.db.execute(
            text(
                "SELECT public.sp_market_screener_enrich_consensus_payload(public.sp_market_screener_enrich_opportunity_payload(public.sp_market_screener_get_user_opportunity(CAST(:user_id AS uuid),CAST(:opportunity_id AS uuid)),CAST(:opportunity_id AS uuid)),CAST(:opportunity_id AS uuid))"
            ),
            {"user_id": user_id, "opportunity_id": opportunity_id},
        ).scalar_one_or_none()
        if p is None:
            return None

        payload = dict(p)
        if not _opportunity_is_fresh(payload, market_time=self.market_time):
            return None
        evidence = payload.get("evidence")
        quant_evidence = evidence.get("quant") if isinstance(evidence, dict) else None
        llm_eligible = (
            quant_evidence.get("llm_eligible")
            if isinstance(quant_evidence, dict)
            else None
        )
        payload["llm_eligible"] = (
            llm_eligible if isinstance(llm_eligible, bool) else None
        )
        return payload

    def list_runs(self, limit=50):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT payload FROM public.sp_market_screener_list_runs(CAST(:limit AS integer)) payload"
                ),
                {"limit": limit},
            ).scalars()
        ]

    def list_snapshots(self, limit=50):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT payload FROM public.sp_market_screener_list_ranking_snapshots(CAST(:limit AS integer)) payload"
                ),
                {"limit": limit},
            ).scalars()
        ]

    def get_snapshot_changes(self, snapshot_id: str | None = None):
        payload = self.db.execute(
            text(
                "SELECT public.sp_market_screener_snapshot_changes("
                "CAST(:snapshot_id AS uuid))"
            ),
            {"snapshot_id": snapshot_id},
        ).scalar_one_or_none()
        return dict(payload) if payload is not None else None

    def list_summary_recipients(self) -> list[str]:
        return [
            str(user_id)
            for user_id in self.db.execute(
                text(
                    "SELECT user_id FROM public.sp_market_screener_summary_recipients()"
                )
            ).scalars()
        ]

    def list_signal_recipients(self, *, instrument_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT * FROM public.sp_market_screener_signal_recipients("
                "CAST(:instrument_id AS uuid))"
            ),
            {"instrument_id": instrument_id},
        ).mappings()
        return [dict(row) for row in rows]

    def list_snapshot_items(self, snapshot_id):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT public.sp_market_screener_enrich_snapshot_consensus_payload(payload,(payload->>'ranking_snapshot_item_id')::uuid) payload FROM public.sp_market_screener_list_snapshot_items(CAST(:snapshot_id AS uuid)) payload"
                ),
                {"snapshot_id": snapshot_id},
            ).scalars()
        ]
