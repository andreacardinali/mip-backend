from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from market_time.normalization import as_utc


class MarketStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExchangeHours:
    exchange: str
    calendar_name: str | None
    timezone_name: str | None


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime
    break_start: datetime | None = None
    break_end: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "opens_at", as_utc(self.opens_at))
        object.__setattr__(self, "closes_at", as_utc(self.closes_at))
        if self.opens_at >= self.closes_at:
            raise ValueError("Session opening must precede closing")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("Both break boundaries are required")
        if self.break_start is not None and self.break_end is not None:
            object.__setattr__(self, "break_start", as_utc(self.break_start))
            object.__setattr__(self, "break_end", as_utc(self.break_end))
            if not self.opens_at < self.break_start < self.break_end < self.closes_at:
                raise ValueError("Break must lie inside the session")


@dataclass(frozen=True)
class CalendarWindow:
    sessions: tuple[MarketSession, ...]
    coverage_start: date
    coverage_end: date
    source: str


@dataclass(frozen=True)
class MarketClock:
    exchange: str
    status: MarketStatus
    observed_at: datetime
    calendar_name: str | None
    timezone_name: str | None
    session: MarketSession | None = None
    next_open: datetime | None = None
    next_close: datetime | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    source: str | None = None
    reason: str | None = None
    previous_session: MarketSession | None = None
