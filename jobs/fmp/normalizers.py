from datetime import date, datetime, timezone
from typing import Any

SOURCE = "FMP"


def normalize_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    return symbol or None


def normalize_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]

    if not text:
        return None

    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(
            timezone.utc
        ).replace(tzinfo=None)

    return parsed


def normalize_earnings(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []

    for row in rows:
        if row.get("epsActual") is not None:
            continue

        symbol = normalize_symbol(row.get("symbol"))
        event_date = normalize_date(row.get("date"))

        if not symbol or not event_date:
            continue

        result.append(
            {
                "symbol": symbol,
                "entity_subtype": "QUARTERLY_EARNINGS",
                "entity_date": event_date,
                "status": "EXPECTED",
                "importance": "MEDIUM",
                "title": f"{symbol} Earnings",
                "source": SOURCE,
                "payload": row,
            }
        )

    return result


def normalize_dividends(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []

    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        event_date = normalize_date(row.get("date"))

        if not symbol or not event_date:
            continue

        try:
            dividend_yield = float(
                row.get("yield") or 0
            )
        except (TypeError, ValueError):
            dividend_yield = 0

        result.append(
            {
                "symbol": symbol,
                "entity_subtype": "CASH_DIVIDEND",
                "entity_date": event_date,
                "status": "CONFIRMED",
                "importance": (
                    "HIGH"
                    if dividend_yield >= 4
                    else "MEDIUM"
                ),
                "title": f"{symbol} Dividend",
                "source": SOURCE,
                "payload": row,
            }
        )

    return result


def normalize_splits(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []

    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        event_date = normalize_date(row.get("date"))

        if not symbol or not event_date:
            continue

        result.append(
            {
                "symbol": symbol,
                "entity_subtype": "STOCK_SPLIT",
                "entity_date": event_date,
                "status": "CONFIRMED",
                "importance": "HIGH",
                "title": f"{symbol} Stock Split",
                "source": SOURCE,
                "payload": row,
            }
        )

    return result


def normalize_filings_8k(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduplicated: dict[
        tuple[str, datetime, str],
        dict[str, Any],
    ] = {}

    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        cik = str(row.get("cik") or "").strip()
        filing_type = str(
            row.get("formType") or "UNKNOWN"
        ).strip().upper()
        filing_date = normalize_date(
            row.get("filingDate")
        )
        accepted_at = normalize_timestamp(
            row.get("acceptedDate")
        )

        if not all(
            (
                symbol,
                cik,
                filing_date,
                accepted_at,
                filing_type,
            )
        ):
            continue

        key = (
            cik,
            accepted_at,
            filing_type,
        )

        deduplicated[key] = {
            "symbol": symbol,
            "filing_type": filing_type,
            "filing_subtype": filing_type,
            "cik": cik,
            "filing_date": filing_date,
            "accepted_at": accepted_at,
            "accession_number": (
                row.get("accessionNumber")
                or None
            ),
            "title": f"{symbol} {filing_type} Filing",
            "source": SOURCE,
            "payload": row,
        }

    return list(deduplicated.values())
