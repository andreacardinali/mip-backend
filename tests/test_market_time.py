from datetime import UTC, date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from market_time.normalization import TemporalNormalizationError, as_utc
from market_time.calendar import CalendarUnavailable, ExchangeCalendarsProvider
from market_time.models import CalendarWindow, ExchangeHours, MarketSession, MarketStatus
from market_time.service import MarketTimeService


@pytest.fixture
def iana_timezone():
    try:
        ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("IANA timezone data unavailable; install tzdata")


def test_aware_offset_becomes_utc():
    assert as_utc(datetime.fromisoformat("2026-07-01T12:00:00+02:00")) == datetime(
        2026, 7, 1, 10, tzinfo=UTC
    )


@pytest.mark.parametrize("value", [date(2026, 7, 1), datetime(2026, 7, 1)])
def test_missing_instant_or_timezone_is_rejected(value):
    with pytest.raises(TemporalNormalizationError):
        as_utc(value)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)],
)
def test_dst_gap_and_overlap_are_rejected(value, iana_timezone):
    with pytest.raises(TemporalNormalizationError):
        as_utc(value, source_timezone="America/New_York")


def test_explicit_source_timezone(iana_timezone):
    assert as_utc(
        datetime(2026, 7, 1, 9, 30), source_timezone="America/New_York"
    ) == datetime(2026, 7, 1, 13, 30, tzinfo=UTC)


def test_explicit_utc_requires_no_iana_data():
    assert as_utc(datetime(2026, 7, 1), source_timezone="UTC") == datetime(
        2026, 7, 1, tzinfo=UTC
    )


def test_unknown_timezone_is_rejected():
    with pytest.raises(TemporalNormalizationError, match="Timezone unavailable"):
        as_utc(datetime(2026, 7, 1), source_timezone="Unknown/Exchange")


def make_service(sessions):
    repository = Mock()
    repository.get_exchange.return_value = ExchangeHours(
        "TEST", "TEST_CALENDAR", "UTC"
    )
    calendars = Mock()
    calendars.window.return_value = CalendarWindow(
        tuple(sessions), date(2026, 1, 1), date(2027, 1, 1), "test-calendar"
    )
    return MarketTimeService(repository, calendars)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize(
    ("at", "status"),
    [
        ("2026-07-01T08:59:59Z", MarketStatus.CLOSED),
        ("2026-07-01T09:00:00Z", MarketStatus.OPEN),
        ("2026-07-01T12:00:00Z", MarketStatus.CLOSED),
        ("2026-07-01T13:00:00Z", MarketStatus.OPEN),
        ("2026-07-01T17:00:00Z", MarketStatus.CLOSED),
    ],
)
def test_session_boundaries_and_break(at, status):
    session = MarketSession(
        date(2026, 7, 1), instant("2026-07-01T09:00:00Z"),
        instant("2026-07-01T17:00:00Z"),
        instant("2026-07-01T12:00:00Z"), instant("2026-07-01T13:00:00Z"),
    )
    next_session = MarketSession(
        date(2026, 7, 2), instant("2026-07-02T09:00:00Z"),
        instant("2026-07-02T17:00:00Z"),
    )
    result = make_service([session, next_session]).snapshot("TEST", at=instant(at))
    assert result.status == status
    assert result.source == "test-calendar"
    assert result.next_open.tzinfo == UTC


def test_overnight_session_uses_session_date_not_instant_date():
    session = MarketSession(
        date(2026, 7, 2), instant("2026-07-01T22:00:00Z"),
        instant("2026-07-02T05:00:00Z"),
    )
    result = make_service([session]).snapshot(
        "TEST", at=instant("2026-07-01T23:00:00Z")
    )
    assert result.status == MarketStatus.OPEN
    assert result.session.session_date == date(2026, 7, 2)


def test_holiday_and_early_close():
    session = MarketSession(
        date(2026, 7, 3), instant("2026-07-03T13:30:00Z"),
        instant("2026-07-03T17:00:00Z"),
    )
    next_session = MarketSession(
        date(2026, 7, 6), instant("2026-07-06T13:30:00Z"),
        instant("2026-07-06T20:00:00Z"),
    )
    service = make_service([session, next_session])
    for at in ("2026-07-03T17:00:00Z", "2026-07-04T15:00:00Z"):
        result = service.snapshot("TEST", at=instant(at))
        assert result.status == MarketStatus.CLOSED
        assert result.next_open == next_session.opens_at


def test_unknown_mapping_does_not_query_calendar():
    service = make_service([])
    service.repository.get_exchange.return_value = None
    assert service.snapshot("UNKNOWN").status == MarketStatus.UNKNOWN
    service.calendars.window.assert_not_called()


def test_unavailable_and_uncovered_calendars_are_unknown():
    service = make_service([])
    assert service.snapshot("TEST", at=instant("2028-07-01T12:00:00Z")).status == (
        MarketStatus.UNKNOWN
    )
    service.calendars.window.side_effect = CalendarUnavailable("missing coverage")
    assert service.snapshot("TEST").status == MarketStatus.UNKNOWN


def test_invalid_session_is_rejected():
    with pytest.raises(ValueError):
        MarketSession(
            date(2026, 7, 1), instant("2026-07-01T17:00:00Z"),
            instant("2026-07-01T09:00:00Z"),
        )


def test_real_calendar_us_holiday_and_early_close():
    pytest.importorskip("exchange_calendars")
    window = ExchangeCalendarsProvider().window(
        "XNYS", instant("2026-11-26T15:00:00Z")
    )
    assert not any(session.session_date == date(2026, 11, 26) for session in window.sessions)
    friday = next(
        session for session in window.sessions
        if session.session_date == date(2026, 11, 27)
    )
    assert friday.closes_at == instant("2026-11-27T18:00:00Z")