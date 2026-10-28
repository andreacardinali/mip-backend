import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_data.capabilities import InstrumentType
from services.entities import _instrument_type


@pytest.fixture
def stub_database(monkeypatch):
    monkeypatch.setitem(sys.modules, "db", SimpleNamespace(SessionLocal=None))


def test_unknown_instrument_type_is_normalized():
    assert _instrument_type("not-known") == InstrumentType.UNKNOWN


def test_etf_instrument_type_is_preserved():
    assert _instrument_type("ETF") == InstrumentType.ETF


def test_full_refresh_job_refreshes_all_existing_entities(monkeypatch, stub_database):
    from jobs.entities import refresh_entities_full
    from services.entities import RefreshMode

    database = object()
    expected = {"status": "SUCCESS"}
    captured = {}

    def fake_refresh(db, **kwargs):
        assert db is database
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(refresh_entities_full, "refresh_entities", fake_refresh)
    monkeypatch.setattr(
        refresh_entities_full, "run_transactional", lambda action: action(database)
    )

    assert refresh_entities_full.run() is expected
    assert captured == {
        "mode": RefreshMode.ALL,
        "symbol": None,
        "create_if_missing": False,
        "full_refresh": True,
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
