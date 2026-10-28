import importlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

import pytest

from jobs.eulerpool_exchange_holidays import sync_holidays
from market_data.capabilities import InstrumentType
from market_data.models import ExchangeReference, InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider
from market_time.repository import SQLHolidayRepository
from scheduler_repository import SchedulerRepository
from scripts.apply_market_time_holidays import migration_statements
from scripts.sync_exchange_holidays import acquire


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
            "jobs.eulerpool_exchange_holidays.acquire", side_effect=outcomes
        ) as fetch,
        patch("jobs.eulerpool_exchange_holidays.SQLHolidayRepository") as repository,
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
        record.name == "jobs.eulerpool_exchange_holidays"
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
        patch("jobs.eulerpool_exchange_holidays.acquire", return_value=Mock()),
        patch("jobs.eulerpool_exchange_holidays.SQLHolidayRepository") as repository,
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
    with patch("jobs.eulerpool_exchange_holidays.SQLHolidayRepository") as repository:
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
        patch("jobs.eulerpool_exchange_holidays.acquire", return_value=Mock()) as fetch,
        patch("jobs.eulerpool_exchange_holidays.SQLHolidayRepository"),
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


def test_holiday_job_catalog_failure_does_not_write():
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
    session_factory.assert_not_called()


def test_holiday_job_manifest():
    path = (
        Path(__file__).resolve().parents[1]
        / "jobs/eulerpool_exchange_holidays.job.json"
    )
    definition = json.loads(path.read_text(encoding="utf-8"))
    assert definition["schema_version"] == 1
    assert definition["job_key"] == "eulerpool_exchange_holidays"
    assert definition["enabled_by_default"] is False
    assert definition["trigger"] == {"type": "cron", "cron_expression": "30 6 * * *"}
    assert callable(importlib.import_module(definition["python_module"]).run)


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
    assert SQLHolidayRepository(db).list_exchanges("Eulerpool") == rows
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
        SQLHolidayRepository(db).list_exchanges("eulerpool")
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
    assert acquire(provider, "XNYS").exchange_code == "XNYS"
    with pytest.raises(ValueError, match="catalog"):
        acquire(provider, "XETR")


def test_holiday_repository_uses_stored_function_and_restores_snapshot():
    snapshot = EulerpoolProvider(
        "key", client=Client([holiday_payload()])
    ).get_exchange_holidays("XNYS")
    db = Mock()
    db.execute.return_value.scalar_one.return_value = "snapshot-id"
    repository = SQLHolidayRepository(db)
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
    assert len(statements) == 4
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
    assert "CASCADE" not in "\n".join(statements)
