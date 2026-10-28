import importlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

import pytest

from http_client import testing as httpx
from market_data.capabilities import InstrumentType
from market_data.exceptions import InstrumentNotFoundError
from market_data.exchange_sync import (
    acquire_holidays,
    sync_exchange_data,
    sync_holidays,
)
from market_data.models import ExchangeReference, InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider
from market_time.repository import SQLExchangeRepository
from scheduler_repository import SchedulerRepository
from scripts.apply_exchange_registry import migration_statements
from scripts.sync_exchanges import main as sync_exchanges_main


def test_exchange_job_commits_catalog_before_holidays_and_reuses_response():
    catalog = (ExchangeReference("XKLSE", name="Bursa Malaysia", source="eulerpool"),)
    provider = Mock()
    provider.get_exchanges.return_value = catalog
    session = MagicMock()
    session_factory = Mock(return_value=session)
    events = []
    session.__enter__.return_value = session
    session.begin.return_value.__exit__.side_effect = lambda *args: events.append(
        "commit"
    )
    with (
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
        patch("market_data.exchange_sync.sync_holidays") as holidays,
    ):
        repository.return_value.sync_catalog.side_effect = (
            lambda value: events.append("catalog") or 1
        )
        repository.return_value.list_exchanges.side_effect = (
            lambda source: events.append("targets") or [{"exchange": "XKLSE"}]
        )
        holidays.side_effect = lambda *args, **kwargs: events.append("holidays") or {
            "status": "SUCCESS"
        }
        result = sync_exchange_data(provider, session_factory)
        repository.return_value.sync_catalog.assert_called_once_with(catalog)
        holidays.assert_called_once_with(
            provider, [{"exchange": "XKLSE"}], session_factory, catalog=catalog
        )
    provider.get_exchanges.assert_called_once_with(strict=True)
    assert events == ["catalog", "targets", "commit", "holidays"]
    assert result == {"status": "SUCCESS", "catalog_upserted": 1}


def test_exchange_job_catalog_save_failure_rolls_back_and_stops():
    provider = Mock()
    provider.get_exchanges.return_value = (
        ExchangeReference("XNYS", source="eulerpool"),
    )
    session = MagicMock()
    with (
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
        patch("market_data.exchange_sync.sync_holidays") as holidays,
    ):
        repository.return_value.sync_catalog.side_effect = ValueError(
            "Noncanonical existing exchanges"
        )
        with pytest.raises(ValueError, match="Noncanonical"):
            sync_exchange_data(provider, Mock(return_value=session))
        repository.return_value.list_exchanges.assert_not_called()
        holidays.assert_not_called()
    assert (
        session.__enter__.return_value.begin.return_value.__exit__.call_args.args[0]
        is ValueError
    )


@pytest.mark.parametrize("invalid_entry", [None, {}, {"code": "BAD"}])
def test_exchange_job_rejects_partial_catalog_before_database_access(invalid_entry):
    provider = EulerpoolProvider(
        "key", client=Client([[{"code": "XNYS"}, invalid_entry]])
    )
    session_factory = Mock()
    with pytest.raises(RuntimeError, match="ValueError"):
        sync_exchange_data(provider, session_factory)
    session_factory.assert_not_called()
    with pytest.raises(ValueError, match="Incomplete"):
        provider.get_exchanges(strict=True)


def test_exchange_job_rejects_empty_catalog_before_database_access():
    provider = EulerpoolProvider("key", client=Client([[]]))
    session_factory = Mock()
    with pytest.raises(RuntimeError, match="ValueError"):
        sync_exchange_data(provider, session_factory)
    session_factory.assert_not_called()


@pytest.mark.parametrize(
    ("response_kwargs", "detail"),
    [
        ({"text": "secret-token"}, "reason=invalid_json"),
        (
            {"json": {"error": "secret-token"}},
            "reason=unexpected_payload_type payload_type=dict",
        ),
        (
            {"json": [{"code": "secret-token"}]},
            "reason=invalid_exchange_code row_index=0 code_type=str code_length=12",
        ),
        (
            {"json": [{"code": 123}]},
            "reason=invalid_exchange_code row_index=0 code_type=int code_length=None",
        ),
    ],
)
def test_holiday_catalog_parse_failures_have_safe_diagnostics(
    response_kwargs, detail, caplog
):
    session_factory = Mock()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, **response_kwargs)
        )
    ) as client:
        provider = EulerpoolProvider("secret-token", client=client)
        with pytest.raises(RuntimeError) as failure:
            sync_holidays(provider, [{"exchange": "XNYS"}], session_factory)
        assert provider._exchange_cache is None
        assert not client.is_closed
    assert detail in caplog.text
    assert "secret-token" not in caplog.text
    assert "secret-token" not in str(failure.value)
    session_factory.assert_not_called()


@pytest.mark.parametrize(
    "invalid_code", ["A" * 21, "secret-token", 123, " XNY ", "ABCDE\nwarning"]
)
def test_holiday_job_skips_invalid_catalog_codes(invalid_code, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"code": "XNYS"},
                {"code": invalid_code},
                {"exchange_code": "XETR"},
            ],
        )

    targets = [
        {"exchange": "NYSE", "exchange_code": "XNYS"},
        {"exchange": "XETR", "exchange_code": "XETR"},
        {"exchange": "unsupported", "exchange_code": "ABCDE"},
    ]
    session_factory = Mock(side_effect=[MagicMock(), MagicMock()])
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch("market_data.exchange_sync.acquire_holidays") as fetch,
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
    ):
        provider = EulerpoolProvider("secret-token", client=client)
        result = sync_holidays(provider, targets, session_factory)
        catalog = provider.get_exchanges()
        assert [item.code for item in catalog] == ["XNYS", "XETR"]
        assert [call.args[1] for call in fetch.call_args_list] == ["XNYS", "XETR"]
        assert repository.return_value.save.call_count == 2
        assert not client.is_closed
    assert result["status"] == "PARTIAL"
    assert result["upserted"] == 2
    assert result["failed"] == 1
    assert result["exchanges"][2]["reason"] == "unresolved_exchange_mapping"
    assert session_factory.call_count == 2
    assert len(requests) == 1
    assert "reason=invalid_exchange_code row_index=1" in caplog.text
    assert "Exchange catalog entry skipped" in caplog.text
    assert "secret-token" not in caplog.text
    if isinstance(invalid_code, str):
        expected_code = "[REDACTED]" if invalid_code == "secret-token" else invalid_code
        assert f"raw_code={expected_code!r}" in caplog.text
    else:
        assert "raw_code=" not in caplog.text


def test_exchange_catalog_normalization_and_cache():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"exchange_code": " xnys ", "code": "ignored", "name": "NYSE"},
                {"code": "xetr", "country": "DE", "timezone": "Europe/Berlin"},
                {},
                None,
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = EulerpoolProvider("secret-token", client=client)
        expected = (
            ExchangeReference(
                code="XNYS",
                name="NYSE",
                source="eulerpool",
                raw_payload={
                    "exchange_code": " xnys ",
                    "code": "ignored",
                    "name": "NYSE",
                },
            ),
            ExchangeReference(
                code="XETR",
                country_code="DE",
                timezone="Europe/Berlin",
                source="eulerpool",
                raw_payload={
                    "code": "xetr",
                    "country": "DE",
                    "timezone": "Europe/Berlin",
                },
            ),
        )
        assert provider.get_exchanges() == expected
        assert provider.get_exchanges() == expected
        assert not client.is_closed
    assert len(requests) == 1
    assert requests[0].url.path == "/api/1/market/exchanges"


@pytest.mark.parametrize(
    ("outcomes", "expected_status"),
    [
        ([Mock(), Mock()], "SUCCESS"),
        ([ValueError("invalid response"), Mock()], "PARTIAL"),
        ([ValueError("invalid response"), ValueError("invalid response")], "FAILED"),
    ],
)
def test_holiday_job_isolates_exchange_failures(outcomes, expected_status):
    targets = [
        {"exchange": "NYSE", "exchange_code": "XNYS"},
        {"exchange": "XETR", "exchange_code": "XETR"},
    ]
    sessions = [MagicMock(), MagicMock()]
    session_factory = Mock(side_effect=sessions)
    provider = Mock()
    catalog = [ExchangeReference(code="XNYS"), ExchangeReference(code="XETR")]
    provider.get_exchanges.return_value = catalog
    with (
        patch(
            "market_data.exchange_sync.acquire_holidays", side_effect=outcomes
        ) as fetch,
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
    ):
        result = sync_holidays(provider, targets, session_factory)
    successful = sum(not isinstance(outcome, Exception) for outcome in outcomes)
    assert result["status"] == expected_status
    assert result["eligible"] == 2
    assert result["upserted"] == successful
    assert result["failed"] == 2 - successful
    assert fetch.call_count == 2
    assert session_factory.call_count == successful
    assert repository.return_value.save.call_count == successful
    assert fetch.call_args_list[1].args == (provider, "XETR")
    assert fetch.call_args_list[1].kwargs == {"catalog": catalog}
    provider.get_exchanges.assert_called_once_with()
    if successful:
        assert repository.return_value.save.call_args.args == ("XETR", outcomes[1])


def test_holiday_job_without_registered_exchanges_does_not_fetch(caplog):
    provider = Mock()
    session_factory = Mock()
    result = sync_holidays(provider, [], session_factory)
    assert result["status"] == "INCOMPLETE"
    assert any(
        record.name == "market_data.exchange_sync"
        and record.levelname == "WARNING"
        and "reason=no_registered_exchanges" in record.getMessage()
        for record in caplog.records
    )
    provider.get_exchanges.assert_not_called()
    provider.get_exchange_holidays.assert_not_called()
    session_factory.assert_not_called()
    db = Mock()
    SchedulerRepository(db).complete_run(
        job_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=result["status"],
        result=result,
        completed_at=datetime.now(UTC),
        error_message="Job completed with partial or incomplete result",
    )
    parameters = db.execute.call_args.args[1]
    assert parameters["status"] == "PARTIAL"
    assert json.loads(parameters["result"]) == result
    assert result["reason"] == "no_registered_exchanges"


@pytest.mark.parametrize("status", ["SUCCESS", "PARTIAL", "FAILED"])
def test_scheduler_repository_preserves_supported_completion_statuses(status):
    db = Mock()
    result = {"status": status}
    SchedulerRepository(db).complete_run(
        job_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=f" {status.lower()} ",
        result=result,
        completed_at=datetime.now(UTC),
    )
    parameters = db.execute.call_args.args[1]
    assert parameters["status"] == status
    assert json.loads(parameters["result"]) == result


def test_holiday_job_rolls_back_failed_save_and_continues():
    targets = [
        {"exchange": "NYSE", "exchange_code": "XNYS"},
        {"exchange": "XETR", "exchange_code": "XETR"},
    ]
    sessions = [MagicMock(), MagicMock()]
    provider = Mock()
    provider.get_exchanges.return_value = [
        ExchangeReference(code="XNYS"),
        ExchangeReference(code="XETR"),
    ]
    with (
        patch("market_data.exchange_sync.acquire_holidays", return_value=Mock()),
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
    ):
        repository.return_value.save.side_effect = [ValueError("secret-token"), "uuid"]
        result = sync_holidays(provider, targets, Mock(side_effect=sessions))
    assert result["status"] == "PARTIAL"
    assert result["upserted"] == 1
    assert "secret-token" not in str(result)
    failed_db = sessions[0].__enter__.return_value
    successful_db = sessions[1].__enter__.return_value
    assert failed_db.begin.return_value.__exit__.call_args.args[0] is ValueError
    assert successful_db.begin.return_value.__exit__.call_args.args[0] is None


@pytest.mark.parametrize(
    "identity",
    [
        {"exchange": "XNYS"},
        {"exchange": "local", "exchangefullname": " New York Stock Exchange "},
        {"exchange": "local", "source_identifiers": ["NYSE", "xnys"]},
        {"exchange": "local", "exchange_codes": ["XNYS"]},
    ],
)
def test_holiday_job_acquires_initial_snapshot_from_catalog(identity):
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [{"code": "XNYS", "name": "New York Stock Exchange"}],
                holiday_payload(),
            ]
        ),
    )
    session_factory = MagicMock()
    with patch("market_data.exchange_sync.SQLExchangeRepository") as repository:
        result = sync_holidays(provider, [identity], session_factory)
    assert result["status"] == "SUCCESS"
    assert result["upserted"] == 1
    exchange, snapshot = repository.return_value.save.call_args.args
    assert exchange == identity["exchange"]
    assert snapshot.exchange_code == "XNYS"
    session_factory.assert_called_once_with()


@pytest.mark.parametrize(
    ("target", "catalog", "reason"),
    [
        ({"exchange": "unknown"}, [], "unresolved_exchange"),
        (
            {"exchange": "NYSE"},
            [ExchangeReference(code="XNYS", name="New York Stock Exchange")],
            "unresolved_exchange",
        ),
        (
            {"exchange": "local", "source_identifiers": ["old"]},
            [ExchangeReference(code="LOCL", name="local")],
            "unresolved_exchange_mapping",
        ),
        (
            {"exchange": "XNYS", "exchange_codes": ["OLDX"]},
            [ExchangeReference(code="XNYS")],
            "unresolved_exchange_mapping",
        ),
        (
            {"exchange": "same"},
            [
                ExchangeReference(code="XNYS", name="same"),
                ExchangeReference(code="XETR", name="same"),
            ],
            "ambiguous_exchange",
        ),
        (
            {"exchange": "local", "source_identifiers": ["XNYS", "XETR"]},
            [ExchangeReference(code="XNYS"), ExchangeReference(code="XETR")],
            "ambiguous_exchange",
        ),
        (
            {"exchange": "XNYS"},
            [ExchangeReference(code="XNYS"), ExchangeReference(code="XNYS")],
            "ambiguous_exchange",
        ),
    ],
)
def test_holiday_job_logs_unresolved_targets_without_fetching(
    target, catalog, reason, caplog
):
    provider = Mock()
    provider.get_exchanges.return_value = catalog
    session_factory = Mock()
    result = sync_holidays(provider, [target], session_factory)
    assert result["status"] == "FAILED"
    assert result["failed"] == 1
    assert result["exchanges"][0]["reason"] == reason
    assert f"reason={reason}" in caplog.text
    provider.get_exchange_holidays.assert_not_called()
    session_factory.assert_not_called()


def test_holiday_job_skips_conflicting_rows_and_continues(caplog):
    provider = Mock()
    provider.get_exchanges.return_value = [
        ExchangeReference(code="XNYS", name="NYSE"),
        ExchangeReference(code="XETR"),
    ]
    catalog = provider.get_exchanges.return_value
    targets = [
        {"exchange": "XNYS"},
        {"exchange": "NYSE"},
        {"exchange": "unknown"},
        {"exchange": "XETR"},
    ]
    session_factory = MagicMock()
    with (
        patch(
            "market_data.exchange_sync.acquire_holidays", return_value=Mock()
        ) as fetch,
        patch("market_data.exchange_sync.SQLExchangeRepository"),
    ):
        result = sync_holidays(provider, targets, session_factory)
    assert result["status"] == "PARTIAL"
    assert result["eligible"] == 4
    assert result["upserted"] == 1
    assert result["failed"] == 3
    assert "reason=conflicting_exchange_mapping" in caplog.text
    assert "reason=unresolved_exchange" in caplog.text
    fetch.assert_called_once_with(provider, "XETR", catalog=catalog)
    provider.get_exchanges.assert_called_once_with()
    session_factory.assert_called_once_with()


@pytest.mark.parametrize("status_code", [403, 404, 429, 500])
def test_holiday_job_exchange_http_failure_is_isolated(status_code, caplog):
    def handler(request):
        if request.url.path.endswith("/market/exchanges"):
            return httpx.Response(200, json=[{"code": "XBER"}, {"code": "XNYS"}])
        if request.url.path.endswith("/market/holidays/XBER"):
            return httpx.Response(status_code, text="secret-token")
        assert request.url.path.endswith("/market/holidays/XNYS")
        return httpx.Response(200, json=holiday_payload())

    targets = [{"exchange": "XBER"}, {"exchange": "XNYS"}]
    session_factory = MagicMock()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch("market_data.exchange_sync.SQLExchangeRepository") as repository,
    ):
        provider = EulerpoolProvider("secret-token", client=client)
        result = sync_holidays(provider, targets, session_factory)
    assert result["status"] == "PARTIAL"
    assert result["failed"] == 1
    assert result["upserted"] == 1
    assert result["exchanges"][0]["http_status"] == status_code
    assert result["exchanges"][0]["error_type"] == (
        "InstrumentNotFoundError" if status_code == 404 else "HTTPStatusError"
    )
    assert result["exchanges"][1]["status"] == "SUCCESS"
    assert f"http_status={status_code}" in caplog.text
    assert "secret-token" not in caplog.text
    assert "secret-token" not in str(result)
    session_factory.assert_called_once_with()
    assert repository.return_value.save.call_count == 1
    assert repository.return_value.save.call_args.args[0] == "XNYS"


def test_holiday_job_catalog_failure_does_not_write(caplog):
    provider = Mock()
    provider.get_exchanges.side_effect = ValueError("secret-token")
    session_factory = Mock()
    with pytest.raises(RuntimeError, match="Unable to retrieve") as error:
        sync_holidays(
            provider,
            [{"exchange": "NYSE", "exchange_code": "XNYS"}],
            session_factory,
        )
    assert "secret-token" not in str(error.value)
    assert "secret-token" not in caplog.text
    assert "error_type=ValueError" in str(error.value)
    assert "error_type=ValueError" in caplog.text
    session_factory.assert_not_called()


@pytest.mark.parametrize("status_code", [401, 403, 404, 429, 500])
def test_holiday_job_catalog_http_failure_is_sanitized(status_code, caplog):
    request = httpx.Request(
        "GET", "https://api.eulerpool.com/api/1/market/exchanges?token=secret-token"
    )
    http_error = httpx.HTTPStatusError(
        f"request failed: {request.url}",
        request=request,
        response=httpx.Response(status_code, request=request),
    )
    provider = Mock()
    if status_code == 404:
        error = InstrumentNotFoundError("secret-token")
        error.__cause__ = http_error
    else:
        error = http_error
    provider.get_exchanges.side_effect = error
    session_factory = Mock()
    with pytest.raises(RuntimeError) as failure:
        sync_holidays(provider, [{"exchange": "XNYS"}], session_factory)
    assert f"http_status={status_code}" in str(failure.value)
    assert f"http_status={status_code}" in caplog.text
    assert f"error_type={type(error).__name__}" in str(failure.value)
    assert "secret-token" not in str(failure.value)
    assert "secret-token" not in caplog.text
    assert failure.value.__suppress_context__
    session_factory.assert_not_called()


def test_holiday_job_catalog_timeout_is_sanitized(caplog):
    provider = Mock()
    provider.get_exchanges.side_effect = httpx.ReadTimeout("secret-token")
    session_factory = Mock()
    with pytest.raises(RuntimeError, match="error_type=ReadTimeout") as failure:
        sync_holidays(provider, [{"exchange": "XNYS"}], session_factory)
    assert "error_type=ReadTimeout" in caplog.text
    assert "secret-token" not in str(failure.value)
    assert "secret-token" not in caplog.text
    session_factory.assert_not_called()


def test_holiday_job_manifest():
    path = Path(__file__).resolve().parents[1] / "jobs/eulerpool_exchange_sync.job.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    assert definition["schema_version"] == 1
    assert definition["job_key"] == "eulerpool_exchange_sync"
    assert definition["job_name"] == "Eulerpool Exchange Sync"
    assert definition["python_module"] == "jobs.eulerpool_exchange_sync"
    assert definition["enabled_by_default"] is False
    assert definition["trigger"] == {"type": "cron", "cron_expression": "30 6 * * *"}
    assert callable(importlib.import_module(definition["python_module"]).run)


def test_exchange_cli_dry_run_does_not_run_job(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sync_exchanges"])
    with patch("jobs.eulerpool_exchange_sync.run") as run:
        sync_exchanges_main()
    run.assert_not_called()
    assert "No HTTP or database access" in capsys.readouterr().out


def test_exchange_cli_apply_runs_complete_sync(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sync_exchanges", "--apply"])
    result = {"status": "PARTIAL", "catalog_upserted": 2, "failed": 1}
    with patch("jobs.eulerpool_exchange_sync.run", return_value=result) as run:
        sync_exchanges_main()
    run.assert_called_once_with()
    assert json.loads(capsys.readouterr().out) == result


def test_holiday_repository_includes_exchanges_without_snapshots():
    db = Mock()
    db.execute.return_value.scalar_one.return_value = 1
    rows = [
        {
            "exchange": "NYSE",
            "exchangefullname": "New York Stock Exchange",
            "exchange_codes": ["XNYS"],
            "source_identifiers": ["NYSE", "XNYS"],
        },
        {
            "exchange": "XETR",
            "exchangefullname": "Xetra",
            "exchange_codes": [],
            "source_identifiers": [],
        },
    ]
    db.execute.return_value.mappings.return_value.all.return_value = rows
    assert SQLExchangeRepository(db).list_exchanges("Eulerpool") == rows
    query, params = db.execute.call_args.args
    assert "FROM public.exchanges e" in str(query)
    assert "exchange_holiday_snapshots" in str(query)
    assert "exchange_source_mappings" in str(query)
    assert "s.active" in str(query)
    assert params == {"source": "eulerpool"}


@pytest.mark.parametrize("active_systems", [0, 2])
def test_holiday_repository_rejects_missing_or_ambiguous_system(active_systems):
    db = Mock()
    db.execute.return_value.scalar_one.return_value = active_systems
    with pytest.raises(ValueError, match="exactly one active"):
        SQLExchangeRepository(db).list_exchanges("eulerpool")
    assert db.execute.call_count == 1


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)

    def request(self, *args, **kwargs):
        return Response(next(self.responses))

    def close(self):
        pass


def test_etf_price_history_is_close_only():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "ticker": "WSPX.MI",
                    }
                ],
                [
                    [1646179200000, 25.19],
                    [1646265600000, 25.20],
                ],
            ]
        ),
    )
    request = type(
        "R",
        (),
        {
            "instrument": InstrumentReference(
                symbol="WSPX.MI",
                isin="XS2427355958",
                instrument_type=InstrumentType.ETF,
            )
        },
    )()
    rows = provider.get_price_history(request)
    assert rows[0].timestamp == 1646179200
    assert rows[0].price == 25.19


def holiday_payload():
    return {
        "exchange": "NYSE",
        "as_of": "2026-10-27",
        "updated_at": "2026-10-27T09:18:08.919Z",
        "holidays": [
            {
                "date": "2025-12-24",
                "name": "Christmas Early Close",
                "exchange": "NYSE",
                "isClosed": None,
                "adjOpenTime": None,
                "adjCloseTime": "13:00",
                "isFullyClosed": False,
            },
            {
                "date": "2026-09-07",
                "name": "Labor Day",
                "exchange": "NYSE",
                "isClosed": True,
                "adjOpenTime": None,
                "adjCloseTime": None,
            },
        ],
    }


def test_holidays_identity_is_requested_code_not_response_label():
    client = Client([holiday_payload()])
    provider = EulerpoolProvider("key", client=client)
    snapshot = provider.get_exchange_holidays(" xnys ")
    assert snapshot.exchange_code == "XNYS"
    assert snapshot.provider_exchange == "NYSE"
    assert snapshot.as_of == date(2026, 10, 27)
    assert snapshot.updated_at.tzinfo == UTC
    assert snapshot.fetched_at.tzinfo == UTC
    assert snapshot.holidays[0].closes_at == time(13)
    assert snapshot.holidays[0].is_closed is False
    assert snapshot.holidays[1].is_closed is True
    assert not hasattr(snapshot, "coverage_end")


@pytest.mark.parametrize("defect", ["identity", "naive", "duplicate", "flags"])
def test_holidays_reject_invalid_snapshot(defect):
    payload = holiday_payload()
    if defect == "identity":
        payload["exchange_code"] = "XETR"
    elif defect == "naive":
        payload["updated_at"] = "2026-10-27T09:18:08"
    elif defect == "duplicate":
        payload["holidays"].append(payload["holidays"][0])
    else:
        payload["holidays"][1]["isFullyClosed"] = False
    provider = EulerpoolProvider("key", client=Client([payload]))
    with pytest.raises(ValueError):
        provider.get_exchange_holidays("XNYS")


def test_exchange_resolution_uses_catalog_without_aliases_or_partial_names():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "exchange_code": "XFRA",
                        "name": "Frankfurt Stock Exchange",
                    },
                    {
                        "exchange_code": "XETR",
                        "name": "Xetra",
                    },
                ]
            ]
        ),
    )
    assert provider._resolve_exchange("xfra").code == "XFRA"
    assert provider._resolve_exchange("Frankfurt Stock Exchange").code == "XFRA"
    assert provider._resolve_exchange("Frankfurt") is None
    assert provider._resolve_exchange("xetra").code == "XETR"


def test_catalog_accepts_eulerpool_five_character_codes():
    provider = EulerpoolProvider(
        "key", client=Client([[{"code": "XKLSE", "name": "Bursa Malaysia"}]])
    )
    assert provider.get_exchanges()[0].code == "XKLSE"
    assert provider._resolve_exchange("xklse").code == "XKLSE"


def test_ambiguous_catalog_names_do_not_resolve():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "code": "XFRA",
                        "name": "Germany",
                    },
                    {
                        "code": "XETR",
                        "name": "Germany",
                    },
                ]
            ]
        ),
    )
    assert provider._resolve_exchange("Germany") is None


def test_acquisition_verifies_requested_code_in_catalog():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [{"code": "XNYS", "name": "New York Stock Exchange"}],
                holiday_payload(),
            ]
        ),
    )
    assert acquire_holidays(provider, "XNYS").exchange_code == "XNYS"
    with pytest.raises(ValueError, match="catalog"):
        acquire_holidays(provider, "XETR")


def test_holiday_repository_uses_stored_function_and_restores_snapshot():
    snapshot = EulerpoolProvider(
        "key", client=Client([holiday_payload()])
    ).get_exchange_holidays("XNYS")
    db = Mock()
    db.execute.return_value.scalar_one.return_value = "snapshot-id"
    repository = SQLExchangeRepository(db)
    assert repository.save("NYSE", snapshot) == "snapshot-id"
    statement, parameters = db.execute.call_args.args
    assert "sp_store_exchange_holidays" in str(statement)
    assert parameters["exchange"] == "NYSE"
    assert parameters["exchange_code"] == "XNYS"
    assert parameters["updated_at"].tzinfo == UTC
    db.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "exchange_code": "XNYS",
        "source": "eulerpool",
        "raw_payload": holiday_payload(),
        "fetched_at": snapshot.fetched_at,
    }
    assert repository.latest("XNYS", "eulerpool") == snapshot


def test_holiday_migration_preserves_existing_exchanges_and_utc_types():
    statements = migration_statements()
    assert len(statements) == 8
    assert "timestamp with time zone" in statements[1]
    assert "as_of date" in statements[1]
    assert "UNIQUE (exchange_code, source, provider_updated_at)" in statements[1]
    assert "REFERENCES public.exchanges(exchange)" in statements[2]
    assert "INSERT INTO public.exchanges" not in statements[3]
    assert "exchange_source_mappings" in statements[3]
    assert "exchange_code <> p_exchange_code" in statements[3]
    assert "ON CONFLICT" in statements[3]
    assert "Provider exchange identity conflicts" in statements[3]
    assert "without changing its version" in statements[3]
    assert "sp_upsert_exchange" in statements[4]
    assert "sp_sync_exchange_catalog" in statements[5]
    assert "CASCADE" not in "\n".join(statements)


def test_holiday_catalog_upgrade_preserves_registry_and_replaces_functions():
    statements = migration_statements(upgrade=True)
    assert len(statements) == 9
    assert (
        "DROP CONSTRAINT exchange_holiday_snapshots_exchange_code_check"
        in statements[1]
    )
    assert "ALTER COLUMN exchange_code TYPE character varying(20)" in statements[2]
    assert "^[A-Z0-9]{4,20}$" in statements[3]
    assert all(
        statement.startswith("CREATE OR REPLACE FUNCTION")
        for statement in statements[4:-1]
    )
    assert "Noncanonical existing exchanges" in statements[6]
    assert "CREATE TABLE" not in "\n".join(statements)
    assert "DELETE FROM" not in "\n".join(statements)


def test_exchange_job_migration_only_preserves_identity_schedule_and_history():
    statements = migration_statements(rename_job_only=True)
    assert len(statements) == 3
    assert statements[-1] == "SELECT public.sp_migrate_exchange_sync_job();"
    function = statements[1]
    assert "LOCK TABLE public.scheduler_jobs IN SHARE ROW EXCLUSIVE MODE" in function
    assert "RAISE EXCEPTION" in function
    assert "WHERE job_key = 'eulerpool_exchange_holidays'" in function
    assert "job_key = 'eulerpool_exchange_sync'" in function
    assert "python_module = 'jobs.eulerpool_exchange_sync'" in function
    assignments = function.split("UPDATE public.scheduler_jobs", 1)[1].split(
        "WHERE", 1
    )[0]
    for field in (
        "job_id",
        "enabled",
        "cron_expression",
        "trigger_type",
        "interval_minutes",
        "created_at",
        "last_run_at",
        "next_run_at",
    ):
        assert field not in assignments
    assert "DELETE" not in function
    assert "INSERT" not in function
    assert "ALTER TABLE" not in "\n".join(statements)


def test_exchange_migration_rejects_combined_modes():
    with pytest.raises(ValueError, match="either registry upgrade"):
        migration_statements(upgrade=True, rename_job_only=True)


def test_holiday_repository_syncs_full_eulerpool_catalog_through_stored_function():
    db = Mock()
    db.execute.return_value.scalar_one.return_value = 1
    payload = {"code": "XKLSE", "name": "Bursa Malaysia", "country": "MY"}
    catalog = (
        ExchangeReference(
            "XKLSE",
            name="Bursa Malaysia",
            country_code="MY",
            source="eulerpool",
            raw_payload=payload,
        ),
    )
    assert SQLExchangeRepository(db).sync_catalog(catalog) == 1
    statement, parameters = db.execute.call_args.args
    assert "sp_sync_exchange_catalog(CAST(:payload AS jsonb))" in str(statement)
    assert json.loads(parameters["payload"]) == [
        {
            "code": "XKLSE",
            "name": "Bursa Malaysia",
            "country": "MY",
            "timezone": None,
            "raw_payload": payload,
        }
    ]


@pytest.mark.parametrize("catalog", [(), (ExchangeReference("XNYS", source="fmp"),)])
def test_holiday_repository_rejects_empty_or_nonauthoritative_catalog(catalog):
    db = Mock()
    with pytest.raises(ValueError, match="nonempty Eulerpool"):
        SQLExchangeRepository(db).sync_catalog(catalog)
    db.execute.assert_not_called()
