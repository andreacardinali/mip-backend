import logging
from datetime import date, timedelta
from typing import Any, Callable

from sqlalchemy import text
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

logger = logging.getLogger(__name__)

CATALYST_SQL = text(
    """
    SELECT sp_upsert_catalyst(
        p_symbol => :symbol,
        p_entity_subtype => :entity_subtype,
        p_entity_date => :entity_date,
        p_entity_status => :status,
        p_importance => :importance,
        p_title => :title,
        p_source => :source,
        p_payload => CAST(:payload AS jsonb)
    )
    """
)

FILING_SQL = text(
    """
    SELECT sp_upsert_filing(
        p_symbol => :symbol,
        p_filing_type => :filing_type,
        p_cik => :cik,
        p_filing_date => :filing_date,
        p_accepted_at => :accepted_at,
        p_title => :title,
        p_source => :source,
        p_raw_payload => CAST(:payload AS jsonb),
        p_filing_subtype => :filing_subtype,
        p_accession_number => :accession_number
    )
    """
)


def _upsert_catalysts(
    db: Session,
    rows: list[dict[str, Any]],
) -> int:
    import json

    for row in rows:
        db.execute(
            CATALYST_SQL,
            {
                **row,
                "payload": json.dumps(row["payload"]),
            },
        )

    return len(rows)


def _upsert_filings(
    db: Session,
    rows: list[dict[str, Any]],
) -> int:
    import json

    for row in rows:
        db.execute(
            FILING_SQL,
            {
                **row,
                "payload": json.dumps(row["payload"]),
            },
        )

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
        upserted = persist(db, normalized_rows)
        db.commit()

        result = {
            "status": "SUCCESS",
            "received": len(received_rows),
            "eligible": len(normalized_rows),
            "upserted": upserted,
            "failed": 0,
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
    today = date.today()
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
    calendar_to = (
        today + timedelta(days=calendar_days)
    ).isoformat()
    filing_from = (
        today - timedelta(days=filing_lookback_days)
    ).isoformat()
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
                normalize_filings_8k,
                _upsert_filings,
            ),
        }

    successful = sum(
        item["status"] == "SUCCESS"
        for item in sources.values()
    )

    if successful == len(sources):
        status = "SUCCESS"
    elif successful == 0:
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
