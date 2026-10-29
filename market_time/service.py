from datetime import UTC, datetime
from typing import Protocol

from market_time.calendar import CalendarProvider, CalendarUnavailable
from market_time.models import ExchangeHours, MarketClock, MarketStatus
from market_time.normalization import as_utc


class ExchangeHoursRepository(Protocol):
    def get_exchange(self, exchange: str) -> ExchangeHours | None: ...


class MarketTimeService:
    def __init__(
        self, repository: ExchangeHoursRepository, calendars: CalendarProvider
    ):
        self.repository = repository
        self.calendars = calendars

    def snapshot(self, exchange: str, *, at: datetime | None = None) -> MarketClock:
        observed_at = as_utc(at if at is not None else datetime.now(UTC))
        hours = self.repository.get_exchange(exchange)
        metadata = {
            "exchange": exchange,
            "observed_at": observed_at,
            "calendar_name": hours.calendar_name if hours else None,
            "timezone_name": hours.timezone_name if hours else None,
        }
        if hours is None or not hours.calendar_name or not hours.timezone_name:
            return MarketClock(
                **metadata,
                status=MarketStatus.UNKNOWN,
                reason="Missing explicit exchange calendar or timezone mapping",
            )
        try:
            window = self.calendars.window(hours.calendar_name, observed_at)
        except CalendarUnavailable as exc:
            return MarketClock(**metadata, status=MarketStatus.UNKNOWN, reason=str(exc))
        metadata.update(
            coverage_start=window.coverage_start,
            coverage_end=window.coverage_end,
            source=window.source,
        )
        if not window.coverage_start <= observed_at.date() < window.coverage_end:
            return MarketClock(
                **metadata,
                status=MarketStatus.UNKNOWN,
                reason="Instant outside calendar coverage",
            )
        sessions = sorted(window.sessions, key=lambda session: session.opens_at)
        metadata["previous_session"] = next(
            (
                session
                for session in reversed(sessions)
                if session.closes_at <= observed_at
            ),
            None,
        )
        for session in sessions:
            if session.opens_at <= observed_at < session.closes_at:
                if (
                    session.break_start is not None
                    and session.break_end is not None
                    and session.break_start <= observed_at < session.break_end
                ):
                    return MarketClock(
                        **metadata,
                        status=MarketStatus.CLOSED,
                        session=session,
                        next_open=session.break_end,
                        next_close=session.closes_at,
                    )
                next_session = next(
                    (future for future in sessions if future.opens_at > observed_at),
                    None,
                )
                return MarketClock(
                    **metadata,
                    status=MarketStatus.OPEN,
                    session=session,
                    next_open=(
                        session.break_end
                        if session.break_start is not None
                        and observed_at < session.break_start
                        else next_session.opens_at if next_session else None
                    ),
                    next_close=(
                        session.break_start
                        if session.break_start is not None
                        and observed_at < session.break_start
                        else session.closes_at
                    ),
                )
        next_session = next(
            (session for session in sessions if session.opens_at > observed_at), None
        )
        if next_session is None:
            return MarketClock(
                **metadata,
                status=MarketStatus.UNKNOWN,
                reason="No next opening within available calendar window",
            )
        return MarketClock(
            **metadata,
            status=MarketStatus.CLOSED,
            next_open=next_session.opens_at,
            next_close=next_session.break_start or next_session.closes_at,
        )
