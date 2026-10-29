from datetime import datetime, timedelta
from typing import Protocol

from market_time.models import CalendarWindow, MarketSession
from market_time.normalization import as_utc


class CalendarUnavailable(ValueError):
    pass


class CalendarProvider(Protocol):
    def window(self, calendar_name: str, at: datetime) -> CalendarWindow: ...


class ExchangeCalendarsProvider:
    def window(self, calendar_name: str, at: datetime) -> CalendarWindow:
        at = as_utc(at)
        try:
            import exchange_calendars
        except ImportError as exc:
            raise CalendarUnavailable("exchange-calendars is not installed") from exc

        try:
            calendar = exchange_calendars.get_calendar(calendar_name)
            coverage_start = calendar.first_session.date()
            coverage_end = calendar.last_session.date()
            if not coverage_start <= at.date() < coverage_end:
                raise CalendarUnavailable("Instant outside calendar coverage")
            start = max(coverage_start, at.date() - timedelta(days=32))
            end = min(coverage_end, at.date() + timedelta(days=32))
            labels = calendar.sessions_in_range(start.isoformat(), end.isoformat())
            sessions = []
            for label in labels:
                row = calendar.schedule.loc[label]
                sessions.append(
                    MarketSession(
                        session_date=label.date(),
                        opens_at=row["open"].to_pydatetime(),
                        closes_at=row["close"].to_pydatetime(),
                        break_start=(
                            None
                            if row.isna()["break_start"]
                            else row["break_start"].to_pydatetime()
                        ),
                        break_end=(
                            None
                            if row.isna()["break_end"]
                            else row["break_end"].to_pydatetime()
                        ),
                    )
                )
            return CalendarWindow(
                sessions=tuple(sessions),
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                source=f"exchange-calendars:{exchange_calendars.__version__}",
            )
        except CalendarUnavailable:
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CalendarUnavailable(f"Calendar unavailable: {calendar_name}") from exc
