import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from config.settings import settings
from db import SessionLocal
from jobs.fmp.client import FMPClient
from jobs.fmp.normalizers import (
    normalize_dividends,
    normalize_earnings,
    normalize_filings_8k,
    normalize_splits,
)
from services.event_persistence import save_catalyst, save_filing

logger = logging.getLogger(__name__)


def _upsert_catalysts(
    db: Session,
    rows: list[dict[str, Any]],
) -> int:
    for row in rows:
        save_catalyst(db, row)

    return len(rows)


def _upsert_filings(
    db: Session,
    rows: list[dict[str, Any]],
) -> int:
    for row in rows:
        save_filing(db, row)

    return len(rows)


def _run_source(
    name: str,
    fetch: Callable[[], list[dict[str, Any]]],
    normalize: Callable[
        [list[dict[str, Any]]],
        list[dict[str, Any]],
    ],
    persist: Callable[
        [Session, list[dict[str, Any]]],
        int,
    ],
) -> dict[str, Any]:
    db = SessionLocal()

    try:
        received_rows = fetch()
        normalized_rows = normalize(received_rows)
        upserted = 0
        failed = 0
        for row in normalized_rows:
            try:
                count = persist(db, [row])
                db.commit()
                upserted += count
            except Exception:
                db.rollback()
                failed += 1
                logger.exception(
                    "FMP item failed source=%s symbol=%s", name, row.get("symbol")
                )

        result = {
            "status": (
                "PARTIAL" if failed and upserted else "FAILED" if failed else "SUCCESS"
            ),
            "received": len(received_rows),
            "eligible": len(normalized_rows),
            "normalization_discarded": len(received_rows) - len(normalized_rows),
            "upserted": upserted,
            "failed": failed,
        }

        logger.info(
            "FMP source completed source=%s result=%s",
            name,
            result,
        )

        return result

    except Exception as exc:
        db.rollback()
        logger.exception(
            "FMP source failed source=%s",
            name,
        )

        return {
            "status": "FAILED",
            "received": 0,
            "eligible": 0,
            "upserted": 0,
            "failed": 1,
            "error": str(exc),
        }

    finally:
        db.close()


def run() -> dict[str, Any]:
    today = datetime.now(UTC).date()
    calendar_days = int(
        settings.get(
            "FMP_CALENDAR_DAYS_FORWARD",
            90,
        )
    )
    filing_lookback_days = int(
        settings.get(
            "FMP_FILINGS_LOOKBACK_DAYS",
            1,
        )
    )
    filing_page_limit = int(
        settings.get(
            "FMP_FILINGS_PAGE_LIMIT",
            20,
        )
    )

    calendar_from = today.isoformat()
    calendar_to = (today + timedelta(days=calendar_days)).isoformat()
    filing_from = (today - timedelta(days=filing_lookback_days)).isoformat()
    filing_to = today.isoformat()

    logger.info(
        "FMP daily sync started calendar=%s..%s filings=%s..%s",
        calendar_from,
        calendar_to,
        filing_from,
        filing_to,
    )

    with FMPClient() as client:
        sources = {
            "earnings": _run_source(
                "earnings",
                lambda: client.earnings_calendar(
                    calendar_from,
                    calendar_to,
                ),
                normalize_earnings,
                _upsert_catalysts,
            ),
            "dividends": _run_source(
                "dividends",
                lambda: client.dividends_calendar(
                    calendar_from,
                    calendar_to,
                ),
                normalize_dividends,
                _upsert_catalysts,
            ),
            "splits": _run_source(
                "splits",
                lambda: client.splits_calendar(
                    calendar_from,
                    calendar_to,
                ),
                normalize_splits,
                _upsert_catalysts,
            ),
            "filings_8k": _run_source(
                "filings_8k",
                lambda: client.filings_8k(
                    filing_from,
                    filing_to,
                    page=0,
                    limit=filing_page_limit,
                ),
                lambda rows: normalize_filings_8k(
                    rows,
                    source_timezone=settings.get("FMP_ACCEPTED_DATE_TIMEZONE") or None,
                ),
                _upsert_filings,
            ),
        }

    successful = sum(item["status"] == "SUCCESS" for item in sources.values())

    if successful == len(sources):
        status = "SUCCESS"
    elif all(item["status"] == "FAILED" for item in sources.values()):
        status = "FAILED"
    else:
        status = "PARTIAL"

    result = {
        "status": status,
        "calendar_from": calendar_from,
        "calendar_to": calendar_to,
        "filing_from": filing_from,
        "filing_to": filing_to,
        **sources,
    }

    logger.info(
        "FMP daily sync completed result=%s",
        result,
    )

    return result


if __name__ == "__main__":
    print(run())
