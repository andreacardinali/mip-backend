import runpy
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from market_time.calendar import CalendarUnavailable, ExchangeCalendarsProvider
from market_time.models import (
    CalendarWindow,
    ExchangeHours,
    MarketSession,
    MarketStatus,
)
from market_time.normalization import TemporalNormalizationError, as_utc
from market_time.repository import SQLExchangeHoursRepository
from market_time.service import MarketTimeService
from scripts.apply_market_time import migration_statements


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


def test_database_connections_request_utc_without_connecting():
    with (
        patch("sqlalchemy.create_engine") as create_engine,
        patch("config.settings.settings", Mock(DATABASE_URL="postgresql+psycopg://test")),
    ):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "db.py"))
    assert create_engine.call_args.kwargs["connect_args"] == {
        "options": "-c timezone=UTC"
    }


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


def test_exchange_repository_uses_only_explicit_mapping():
    db = Mock()
    db.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "exchange": "TEST", "calendar_name": None, "timezone_name": "UTC"
    }
    result = SQLExchangeHoursRepository(db).get_exchange("TEST")
    assert result == ExchangeHours("TEST", None, "UTC")
    assert db.execute.call_args.args[1] == {"exchange": "TEST"}
    db.execute.return_value.mappings.return_value.one_or_none.return_value = None
    assert SQLExchangeHoursRepository(db).get_exchange("MISSING") is None


def test_market_time_migration_is_scoped_and_does_not_backfill():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    preflight, definition = migration_statements(schema)
    assert "ADD COLUMN IF NOT EXISTS calendar_name" in preflight
    assert "RESTRICT" in preflight
    assert "proacl IS NOT NULL" in preflight
    assert "CASCADE" not in preflight
    assert "UPDATE public.filings" not in preflight
    assert "actual_type IS DISTINCT FROM expected.data_type" in preflight
    assert "No automatic historical conversion is allowed" in preflight
    assert definition.startswith("CREATE OR REPLACE FUNCTION public.sp_upsert_filing")
    assert "p_accepted_at timestamp with time zone" in definition


def test_market_time_migration_rejects_wrong_signature():
    with pytest.raises(ValueError):
        migration_statements(
            "CREATE FUNCTION public.sp_upsert_filing(p_accepted_at timestamp without time zone)\n$$;"
        )


def test_clock_api_rejects_naive_instants_and_serializes_utc():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    security = Mock(CurrentUser=dict, get_current_user=lambda: {})
    database = MagicMock()
    db = database.SessionLocal.return_value.__enter__.return_value
    db.execute.return_value.mappings.return_value.one_or_none.return_value = None
    with patch.dict("sys.modules", {"db": database, "auth.security": security}):
        namespace = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "routers" / "market_time.py")
        )
    app = FastAPI()
    app.include_router(namespace["router"], prefix="/api/market-time")
    client = TestClient(app)
    assert client.get(
        "/api/market-time/exchanges/TEST", params={"at": "2026-07-01T12:00:00"}
    ).status_code == 422
    database.SessionLocal.assert_not_called()
    response = client.get(
        "/api/market-time/exchanges/TEST",
        params={"at": "2026-07-01T12:00:00+02:00"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "UNKNOWN"
    assert response.json()["observed_at"] == "2026-07-01T10:00:00Z"


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


@pytest.mark.parametrize(
    ("session_day", "opens_at", "closes_at"),
    [
        ("2026-03-06", "2026-03-06T14:30:00Z", "2026-03-06T21:00:00Z"),
        ("2026-03-09", "2026-03-09T13:30:00Z", "2026-03-09T20:00:00Z"),
        ("2026-10-30", "2026-10-30T13:30:00Z", "2026-10-30T20:00:00Z"),
        ("2026-11-02", "2026-11-02T14:30:00Z", "2026-11-02T21:00:00Z"),
    ],
)
def test_real_calendar_us_dst_boundaries(session_day, opens_at, closes_at):
    pytest.importorskip("exchange_calendars")
    window = ExchangeCalendarsProvider().window("XNYS", instant(opens_at))
    session = next(
        session for session in window.sessions
        if session.session_date == date.fromisoformat(session_day)
    )
    assert session.opens_at == instant(opens_at)
    assert session.closes_at == instant(closes_at)
    assert session.opens_at.tzinfo == UTC
    assert session.closes_at.tzinfo == UTC


def test_real_calendar_hong_kong_intraday_break():
    pytest.importorskip("exchange_calendars")
    window = ExchangeCalendarsProvider().window(
        "XHKG", instant("2026-07-02T04:30:00Z")
    )
    session = next(
        session for session in window.sessions
        if session.session_date == date(2026, 7, 2)
    )
    assert session.opens_at == instant("2026-07-02T01:30:00Z")
    assert session.break_start == instant("2026-07-02T04:00:00Z")
    assert session.break_end == instant("2026-07-02T05:00:00Z")
    assert session.closes_at == instant("2026-07-02T08:00:00Z")
    assert session.break_start.tzinfo == UTC
    assert session.break_end.tzinfo == UTC