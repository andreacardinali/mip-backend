import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

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


def test_superseded_jobs_have_no_recurring_definitions():
    from job_runtime.requests import SUPERSEDED_JOBS

    root = Path(__file__).resolve().parents[1] / "jobs"
    definitions = {
        json.loads(path.read_text(encoding="utf-8"))["job_key"]
        for path in root.rglob("*.job.json")
    }
    assert not definitions.intersection(SUPERSEDED_JOBS)
    assert set(SUPERSEDED_JOBS.values()) <= definitions


@pytest.fixture
def scheduler_catalog(monkeypatch):
    import scheduler_bootstrap

    definition = {
        "schema_version": 1,
        "job_key": "market_screener_pipeline",
        "job_name": "Screener pipeline",
        "description": "Runs the selected screener phases.",
        "python_module": "jobs.market_screener_pipeline.worker",
        "trigger": {"type": "cron", "cron_expression": "0 5 * * *"},
        "enabled_by_default": True,
    }
    db = MagicMock()
    repository = MagicMock()
    repository.register_job.return_value = {"job_id": "defined", "action": "UNCHANGED"}
    monkeypatch.setattr(scheduler_bootstrap, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler_bootstrap, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(scheduler_bootstrap, "discover_job_definitions", lambda root: [Path("pipeline.job.json")])
    monkeypatch.setattr(scheduler_bootstrap, "load_job_definition", lambda path, **kwargs: definition)
    return scheduler_bootstrap, db, repository


@pytest.mark.parametrize("removed_jobs", [[], [{"job_key": "removed_job", "job_id": UUID(int=2)}]])
def test_scheduler_catalog_prunes_only_after_registration(scheduler_catalog, removed_jobs):
    bootstrap, db, repository = scheduler_catalog
    repository.prune_undefined_jobs.return_value = removed_jobs
    bootstrap.bootstrap_scheduler_jobs(jobs_root=Path("jobs"), timezone="UTC")
    repository.prune_undefined_jobs.assert_called_once_with(job_keys=["market_screener_pipeline"])
    repository.register_job.assert_called_once()
    assert [call[0] for call in repository.mock_calls] == ["register_job", "prune_undefined_jobs"]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    db.close.assert_called_once()


def test_scheduler_catalog_imports_description_on_each_bootstrap(scheduler_catalog):
    bootstrap, db, repository = scheduler_catalog
    definition = bootstrap.load_job_definition(Path("pipeline.job.json"), timezone="UTC")
    bootstrap.bootstrap_scheduler_jobs(jobs_root=Path("jobs"), timezone="UTC")
    assert repository.register_job.call_args.kwargs["description"] == definition["description"]
    definition["description"] = "Updated screener description."
    bootstrap.bootstrap_scheduler_jobs(jobs_root=Path("jobs"), timezone="UTC")
    assert repository.register_job.call_args.kwargs["description"] == definition["description"]
    assert db.commit.call_count == 2


@pytest.mark.parametrize("description", [None, "", "   ", 123])
def test_scheduler_catalog_rejects_invalid_description(scheduler_catalog, description):
    bootstrap, _, _ = scheduler_catalog
    definition = dict(bootstrap.load_job_definition(Path("pipeline.job.json"), timezone="UTC"))
    definition["description"] = description
    with pytest.raises(bootstrap.JobDefinitionError, match="invalid description"):
        bootstrap.validate_job_definition(definition, path=Path("pipeline.job.json"), timezone="UTC")


def test_scheduler_repository_passes_description_to_registration():
    from scheduler_repository import SchedulerRepository

    db = MagicMock()
    expected = {"job_id": "defined", "action": "UPDATED"}
    db.execute.return_value.mappings.return_value.one.return_value = expected
    repository = SchedulerRepository(db)
    assert repository.register_job(
        job_key="defined", job_name="Defined job", description="Updated description.",
        python_module="jobs.worker", trigger_type="interval", cron_expression=None,
        interval_minutes=15, enabled_by_default=True,
    ) == expected
    statement, parameters = db.execute.call_args.args
    assert ":description" in str(statement)
    assert parameters["description"] == "Updated description."
    db.commit.assert_not_called()


@pytest.mark.parametrize("failure", ["register_job", "prune_undefined_jobs"])
def test_scheduler_catalog_rolls_back_on_failure(scheduler_catalog, failure):
    bootstrap, db, repository = scheduler_catalog
    getattr(repository, failure).side_effect = RuntimeError("catalog failure")
    with pytest.raises(RuntimeError, match="catalog failure"):
        bootstrap.bootstrap_scheduler_jobs(jobs_root=Path("jobs"), timezone="UTC")
    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    db.close.assert_called_once()
    if failure == "register_job":
        repository.prune_undefined_jobs.assert_not_called()


@pytest.mark.parametrize("problem", ["empty", "invalid", "duplicate"])
def test_scheduler_catalog_validates_before_database_access(scheduler_catalog, monkeypatch, problem):
    bootstrap, db, repository = scheduler_catalog
    session_factory = MagicMock()
    monkeypatch.setattr(bootstrap, "SessionLocal", session_factory)
    if problem == "empty":
        monkeypatch.setattr(bootstrap, "discover_job_definitions", lambda root: [])
    elif problem == "invalid":
        loader = MagicMock(side_effect=bootstrap.JobDefinitionError("invalid definition"))
        monkeypatch.setattr(bootstrap, "load_job_definition", loader)
    else:
        monkeypatch.setattr(bootstrap, "discover_job_definitions", lambda root: [Path("first.job.json"), Path("second.job.json")])
    with pytest.raises(bootstrap.JobDefinitionError):
        bootstrap.bootstrap_scheduler_jobs(jobs_root=Path("jobs"), timezone="UTC")
    session_factory.assert_not_called()
    repository.register_job.assert_not_called()
    repository.prune_undefined_jobs.assert_not_called()


def test_scheduler_catalog_repository_uses_atomic_pruning_routine():
    from scheduler_repository import SchedulerRepository

    db = MagicMock()
    catalog = [{"job_key": "removed_job", "enabled": True}]
    db.execute.return_value.mappings.return_value.all.return_value = catalog
    repository = SchedulerRepository(db)
    assert repository.prune_undefined_jobs(job_keys=["defined_job"]) == catalog

    calls = db.execute.call_args_list
    assert len(calls) == 1
    assert str(calls[0].args[0]) == "SELECT * FROM public.sp_scheduler_prune_undefined_jobs(CAST(:job_keys AS text[]))"
    assert calls[0].args[1] == {"job_keys": ["defined_job"]}
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("problem", ["missing", "root_permission", "nested_permission", "unreadable"])
def test_scheduler_catalog_access_failure_does_not_open_database(tmp_path, monkeypatch, problem):
    import scheduler_bootstrap

    session_factory = MagicMock()
    monkeypatch.setattr(scheduler_bootstrap, "SessionLocal", session_factory)
    root = tmp_path / "jobs"
    if problem != "missing":
        root.mkdir()
        (root / "first.job.json").write_text("{}", encoding="utf-8")
    if problem in {"root_permission", "nested_permission"}:
        denied = root if problem == "root_permission" else root / "nested"
        if problem == "nested_permission":
            denied.mkdir()
        original_scandir = scheduler_bootstrap.os.scandir

        def scandir(directory):
            if Path(directory) == denied:
                raise PermissionError("denied")
            return original_scandir(directory)

        monkeypatch.setattr(scheduler_bootstrap.os, "scandir", scandir)
    elif problem == "unreadable":
        monkeypatch.setattr(Path, "read_text", MagicMock(side_effect=PermissionError("denied")))
    with pytest.raises(scheduler_bootstrap.JobDefinitionError):
        scheduler_bootstrap.bootstrap_scheduler_jobs(jobs_root=root, timezone="UTC")
    session_factory.assert_not_called()


def test_scheduler_definition_scan_includes_nested_files(tmp_path):
    from scheduler_bootstrap import discover_job_definitions

    nested = tmp_path / "nested"
    nested.mkdir()
    first = tmp_path / "first.job.json"
    second = nested / "second.job.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    (nested / "ignored.json").write_text("{}", encoding="utf-8")
    assert discover_job_definitions(tmp_path) == sorted([first, second])


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
