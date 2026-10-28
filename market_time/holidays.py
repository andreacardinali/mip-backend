from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from market_time.normalization import as_utc


@dataclass(frozen=True)
class HolidayException:
    session_date: date
    name: str
    is_closed: bool | None
    opens_at: time | None
    closes_at: time | None


@dataclass(frozen=True)
class HolidaySnapshot:
    exchange_code: str
    source: str
    provider_exchange: str
    as_of: date
    updated_at: datetime
    fetched_at: datetime
    holidays: tuple[HolidayException, ...]
    raw_payload: dict[str, Any]


def normalize_exchange_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Exchange code must be a string")
    code = value.strip().upper()
    if not 4 <= len(code) <= 20 or not code.isascii() or not code.isalnum():
        raise ValueError("An explicit 4-20 character exchange code is required")
    return code


def _local_time(value: Any) -> time | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Adjusted trading time must be a local time string")
    parsed = time.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("Adjusted trading time must not contain a timezone")
    return parsed


def normalize_holidays(
    exchange_code: str,
    payload: dict[str, Any],
    *,
    source: str,
    fetched_at: datetime | None = None,
) -> HolidaySnapshot:
    code = normalize_exchange_code(exchange_code)
    if not isinstance(payload, dict):
        raise ValueError("Holiday response must be an object")
    provider_exchange = payload.get("exchange")
    if not isinstance(provider_exchange, str) or not provider_exchange.strip():
        raise ValueError("Holiday response must identify its provider exchange")
    if "exchange_code" in payload:
        if normalize_exchange_code(payload["exchange_code"]) != code:
            raise ValueError("Response exchange code conflicts with the request")
    rows = payload.get("holidays")
    if not isinstance(rows, list):
        raise ValueError("Holiday response must contain a holidays list")
    holidays = []
    seen_dates = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Holiday entry must be an object")
        if row.get("exchange", provider_exchange) != provider_exchange:
            raise ValueError("Holiday exchange conflicts with response identity")
        session_date = date.fromisoformat(row["date"])
        if session_date in seen_dates:
            raise ValueError("Duplicate holiday date in response")
        seen_dates.add(session_date)
        flags = (row.get("isClosed"), row.get("isFullyClosed"))
        if any(flag is not None and type(flag) is not bool for flag in flags):
            raise ValueError("Holiday closure flags must be boolean or null")
        is_closed, fully_closed = flags
        if is_closed is not None and fully_closed is not None:
            if is_closed != fully_closed:
                raise ValueError("Conflicting holiday closure flags")
        closed = is_closed if is_closed is not None else fully_closed
        opens_at = _local_time(row.get("adjOpenTime"))
        closes_at = _local_time(row.get("adjCloseTime"))
        if closed is True and (opens_at is not None or closes_at is not None):
            raise ValueError("Fully closed holiday cannot have adjusted hours")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Holiday name is required")
        holidays.append(
            HolidayException(session_date, name, closed, opens_at, closes_at)
        )
    return HolidaySnapshot(
        exchange_code=code,
        source=source,
        provider_exchange=provider_exchange.strip(),
        as_of=date.fromisoformat(payload["as_of"]),
        updated_at=as_utc(datetime.fromisoformat(payload["updated_at"])),
        fetched_at=as_utc(fetched_at if fetched_at is not None else datetime.now(UTC)),
        holidays=tuple(sorted(holidays, key=lambda item: item.session_date)),
        raw_payload=payload,
    )
