import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import settings
from market_data.capabilities import InstrumentType
from market_data.exceptions import AllProvidersFailedError, InstrumentNotFoundError
from market_data.models import InstrumentReference, NewsArticle
from market_data.service import MarketDataService

logger = logging.getLogger(__name__)


def _as_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(settings.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _as_bool(name: str, default: bool) -> bool:
    return str(settings.get(name, str(default))).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _instrument_type(value: str | None) -> InstrumentType:
    clean = str(value or "UNKNOWN").strip().upper()
    try:
        return InstrumentType(clean)
    except ValueError:
        return InstrumentType.UNKNOWN


def _published_at(article: NewsArticle) -> datetime:
    value = article.published_at
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _external_id(article: NewsArticle) -> str:
    if article.external_id:
        return str(article.external_id)

    stable_value = "|".join(
        (
            article.source or "",
            article.source_name or "",
            article.url or "",
            article.headline,
            _published_at(article).isoformat(),
        )
    )
    return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()


def _article_payload(article: NewsArticle) -> dict[str, Any]:
    published_at = _published_at(article)
    raw_json = asdict(article)
    raw_json["published_at"] = published_at.isoformat()
    raw_json["related_symbols"] = list(article.related_symbols)

    return {
        "external_id": _external_id(article),
        "headline": article.headline,
        "summary": article.summary,
        "url": article.url,
        "published_at": published_at.isoformat(),
        "source_name": article.source_name or article.source or "UNKNOWN",
        "provider": article.source or "UNKNOWN",
        "category": article.category,
        "image_url": article.image_url,
        "related_symbols": list(article.related_symbols),
        "raw_json": raw_json,
    }


def _load_targets(
    db: Session,
    *,
    include_all: bool,
    limit: int,
) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
            SELECT *
            FROM public.sp_news_list_ingestion_targets(
                :include_all,
                :limit
            )
            """
            ),
            {
                "include_all": include_all,
                "limit": limit,
            },
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _persist_article(
    db: Session,
    *,
    entity_id: str,
    article: NewsArticle,
) -> dict[str, Any]:
    result = db.execute(
        text(
            """
            SELECT public.sp_news_upsert_article(
                CAST(:entity_id AS uuid),
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "entity_id": entity_id,
            "payload": json.dumps(
                _article_payload(article),
                ensure_ascii=False,
            ),
        },
    ).scalar_one()

    if isinstance(result, dict):
        return result
    return json.loads(result)


def _is_empty_news_result(error: AllProvidersFailedError) -> bool:
    return bool(error.failures) and all(
        isinstance(failure, InstrumentNotFoundError) for _, failure in error.failures
    )


def ingest_news(
    db: Session,
    *,
    market_data_service: MarketDataService,
    target_limit: int | None = None,
    news_limit: int | None = None,
    lookback_hours: int | None = None,
    include_all_instruments: bool | None = None,
) -> dict[str, int]:
    target_limit = target_limit or _as_int(
        "NEWS_INGESTION_TARGET_LIMIT",
        500,
    )
    news_limit = news_limit or _as_int(
        "NEWS_INGESTION_NEWS_LIMIT",
        20,
    )
    lookback_hours = lookback_hours or _as_int(
        "NEWS_INGESTION_LOOKBACK_HOURS",
        72,
    )
    include_all = (
        _as_bool("NEWS_INGESTION_INCLUDE_ALL_INSTRUMENTS", False)
        if include_all_instruments is None
        else include_all_instruments
    )

    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    targets = _load_targets(
        db,
        include_all=include_all,
        limit=target_limit,
    )

    stats = {
        "targets": len(targets),
        "processed": 0,
        "no_news": 0,
        "provider_failed": 0,
        "received": 0,
        "skipped_old": 0,
        "inserted": 0,
        "updated": 0,
        "associations_inserted": 0,
        "persistence_failed": 0,
        "failed": 0,
    }

    for target in targets:
        symbol = str(target.get("symbol") or "").strip()
        entity_id = str(target.get("entity_id") or "").strip()

        if not symbol or not entity_id:
            stats["failed"] += 1
            logger.warning(
                "Skipping invalid news target target=%s",
                target,
            )
            continue

        reference = InstrumentReference(
            symbol=symbol,
            isin=target.get("isin"),
            instrument_type=_instrument_type(target.get("instrument_type")),
            currency=target.get("currency_code"),
            exchange_code=target.get("exchange"),
        )

        try:
            articles = (
                market_data_service.get_news(
                    reference,
                    limit=news_limit,
                )
                or []
            )
        except AllProvidersFailedError as error:
            if _is_empty_news_result(error):
                stats["processed"] += 1
                stats["no_news"] += 1
                logger.info(
                    "No news found entity_id=%s symbol=%s",
                    entity_id,
                    symbol,
                )
                continue

            stats["provider_failed"] += 1
            stats["failed"] += 1
            logger.exception(
                "News provider failure entity_id=%s symbol=%s",
                entity_id,
                symbol,
            )
            continue
        except Exception:
            stats["provider_failed"] += 1
            stats["failed"] += 1
            logger.exception(
                "Unexpected news provider failure entity_id=%s symbol=%s",
                entity_id,
                symbol,
            )
            continue

        stats["processed"] += 1
        stats["received"] += len(articles)

        logger.info(
            "News fetched entity_id=%s symbol=%s articles=%s",
            entity_id,
            symbol,
            len(articles),
        )

        for article in articles:
            if _published_at(article) < cutoff:
                stats["skipped_old"] += 1
                continue

            try:
                with db.begin_nested():
                    outcome = _persist_article(
                        db,
                        entity_id=entity_id,
                        article=article,
                    )
            except Exception:
                stats["persistence_failed"] += 1
                stats["failed"] += 1
                logger.exception(
                    "News persistence failed " "entity_id=%s symbol=%s external_id=%s",
                    entity_id,
                    symbol,
                    article.external_id,
                )
                continue

            if bool(outcome.get("inserted")):
                stats["inserted"] += 1
            else:
                stats["updated"] += 1

            stats["associations_inserted"] += int(
                outcome.get("associations_inserted", 0)
            )

    logger.info("News ingestion stats=%s", stats)
    return stats
