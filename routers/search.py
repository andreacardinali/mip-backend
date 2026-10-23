import json
import re

from fastapi import APIRouter, Depends
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data

router = APIRouter()

_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _is_isin(value: str) -> bool:
    return bool(_ISIN_PATTERN.fullmatch(value.strip().upper()))


@router.get("")
async def search_symbols(
    q: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    query = q.strip()
    if not query:
        return {"source": "EMPTY", "results": []}

    cache_key = query.lower()
    isin_search = _is_isin(query)
    exact_symbol_search = bool(re.fullmatch(r"[A-Za-z0-9^=._-]+", query))
    db = SessionLocal()

    try:
        search_result = db.execute(
            text(
                """
                SELECT sp_search_assets(
                    :query,
                    20,
                    24
                )
                """
            ),
            {"query": cache_key},
        ).scalar()

        # A symbol/name search can stop at LOCAL or CACHE. An ISIN identifies
        # a product that can have multiple listings, so local data must not
        # hide provider results for the other currencies/exchanges.
        if (
            not isin_search
            and not exact_symbol_search
            and search_result
            and search_result.get("source") != "MISS"
        ):
            return search_result

        provider_results = [
            result.to_dict() for result in market_data.search(query, limit=20)
        ]
        local_results = (
            search_result.get("results", [])
            if search_result and search_result.get("source") != "MISS"
            else []
        )
        results, seen = [], set()
        for result in [*provider_results, *local_results]:
            symbol = str(result.get("symbol") or "").strip().upper()
            source = str(
                result.get("data_source") or result.get("source") or ""
            ).upper()
            exchange = str(
                result.get("exchange") or result.get("exchange_code") or ""
            ).upper()
            key = (symbol, source, exchange)
            if not symbol or key in seen:
                continue
            seen.add(key)
            results.append(result)
            if len(results) >= 20:
                break

        db.execute(
            text(
                """
                SELECT sp_upsert_market_cache(
                    :cache_key,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "cache_key": cache_key,
                "payload": json.dumps(results),
            },
        )
        db.commit()

        return {"source": "PROVIDER", "results": results}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
