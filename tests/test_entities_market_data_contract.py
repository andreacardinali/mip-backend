import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_data.capabilities import InstrumentType
from services import entities
from services.entities import _instrument_type


@pytest.fixture
def stub_database(monkeypatch):
    monkeypatch.setitem(sys.modules, "db", SimpleNamespace(SessionLocal=None))


def test_unknown_instrument_type_is_normalized():
    assert _instrument_type("not-known") == InstrumentType.UNKNOWN


def test_etf_instrument_type_is_preserved():
    assert _instrument_type("ETF") == InstrumentType.ETF


@pytest.mark.parametrize("full_refresh", [False, True])
def test_refresh_job_uses_service_transaction_boundaries(
    monkeypatch, stub_database, full_refresh
):
    from jobs.entities import refresh_entities, refresh_entities_full
    from services.entities import RefreshMode

    job = refresh_entities_full if full_refresh else refresh_entities
    closed = []
    database = SimpleNamespace(close=lambda: closed.append(True))
    expected = {"status": "SUCCESS"}
    captured = {}

    def fake_refresh(db, **kwargs):
        assert db is database
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(job, "refresh_entities", fake_refresh)
    monkeypatch.setattr(job, "SessionLocal", lambda: database)

    assert job.run() is expected
    assert closed == [True]
    assert captured == {
        "mode": RefreshMode.ALL if full_refresh else RefreshMode.MISSING,
        "symbol": None,
        "create_if_missing": False,
        "full_refresh": full_refresh,
    }


def test_full_refresh_job_is_disabled_by_default(stub_database):
    from jobs.entities import refresh_entities_full

    path = (
        Path(__file__).resolve().parents[1] / "jobs" / "refresh_entities_full.job.json"
    )
    definition = json.loads(path.read_text(encoding="utf-8"))
    existing = json.loads(
        path.with_name("refresh_entities.job.json").read_text(encoding="utf-8")
    )

    assert definition["schema_version"] == existing["schema_version"]
    assert definition["trigger"] == existing["trigger"]
    assert definition["enabled_by_default"] is False
    assert definition["job_key"] != existing["job_key"]
    assert definition["python_module"] == refresh_entities_full.__name__


@pytest.mark.parametrize("fail_after_first", [False, True])
def test_cleanup_job_commits_each_entity(monkeypatch, stub_database, fail_after_first):
    from jobs.entities import cleanup_entities

    events = []
    outcomes = iter(["deleted", "referenced", "skipped"])

    def execute(statement, parameters=None):
        if parameters is None:
            assert (
                str(statement)
                == "SELECT entity_id FROM public.sp_list_orphan_entities()"
            )
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: ["first", "second", "third"]
                )
            )
        assert str(statement) == "SELECT public.sp_cleanup_orphan_entity(:entity_id)"
        events.append(parameters["entity_id"])
        if fail_after_first and parameters["entity_id"] == "second":
            raise RuntimeError("cleanup failed")
        return SimpleNamespace(scalar_one=lambda: next(outcomes))

    database = SimpleNamespace(
        execute=execute,
        commit=lambda: events.append("commit"),
        rollback=lambda: events.append("rollback"),
        close=lambda: events.append("close"),
    )
    monkeypatch.setattr(cleanup_entities, "SessionLocal", lambda: database)
    if fail_after_first:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            cleanup_entities.run()
        assert events == ["first", "commit", "second", "rollback", "close"]
    else:
        assert cleanup_entities.run() == {
            "status": "SUCCESS",
            "candidates": 3,
            "deleted": 1,
            "referenced": 1,
            "skipped": 1,
        }
        assert events == [
            "first",
            "commit",
            "second",
            "commit",
            "third",
            "commit",
            "close",
        ]


def test_cleanup_job_definition_is_disabled_by_default(stub_database):
    from jobs.entities import cleanup_entities

    path = Path(__file__).resolve().parents[1] / "jobs" / "cleanup_entities.job.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    assert definition["schema_version"] == 1
    assert definition["job_key"] == "cleanup_entities"
    assert definition["python_module"] == cleanup_entities.__name__
    assert definition["enabled_by_default"] is False
    assert definition["trigger"] == {
        "type": "cron",
        "cron_expression": "30 4 * * *",
    }


@pytest.mark.parametrize(
    "mode", [entities.RefreshMode.MISSING, entities.RefreshMode.ALL]
)
@pytest.mark.parametrize("failure_stage", ["persist", "commit"])
def test_refresh_commits_each_update_and_recovers_from_failure(
    monkeypatch, mode, failure_stage
):
    events = []
    rows = [
        SimpleNamespace(symbol=symbol, entity_id=symbol, instrument_id=symbol)
        for symbol in ("FIRST", "FAIL", "LAST")
    ]

    def commit():
        if failure_stage == "commit" and events and events[-1] == "FAIL":
            raise RuntimeError("failed commit")
        events.append("commit")

    database = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchall=lambda: rows),
        begin_nested=nullcontext,
        commit=commit,
        rollback=lambda: events.append("rollback"),
    )

    def profile(reference, **kwargs):
        events.append(reference.symbol)
        return {"symbol": reference.symbol}

    def persist(db, payload, **kwargs):
        if payload["symbol"] == "FAIL" and failure_stage == "persist":
            raise RuntimeError("failed update")
        return SimpleNamespace(action="UPDATED")

    monkeypatch.setattr(entities, "_profile_payload", profile)
    monkeypatch.setattr(entities, "upsert_asset_profile", persist)

    result = entities.refresh_entities(database, mode=mode)

    assert result["status"] == "PARTIAL"
    assert result["updated"] == 2
    assert result["failed"] == 1
    assert events == ["commit", "FIRST", "commit", "FAIL", "rollback", "LAST", "commit"]
