import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from routers import portfolios, positions, watchlist_items

USER_ID = "00000000-0000-0000-0000-000000000001"
JOB_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000003"


@pytest.mark.parametrize("operation", ["entities", "cache", "prices"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_operational_http_apis_return_scheduler_ticket(monkeypatch, operation, authenticated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth.security import get_current_user
    from routers import admin, market_data

    module = market_data if operation == "prices" else admin
    path = {
        "entities": "/entities/refresh",
        "cache": "/cache/cleanup",
        "prices": "/prices/refresh",
    }[operation]
    db = MagicMock()
    session_factory = MagicMock(return_value=db)
    repository = MagicMock()
    ticket = {"request_id": JOB_ID, "job_run_id": RUN_ID, "status": "QUEUED"}
    repository.request_run.return_value = ticket
    monkeypatch.setattr(module, "SessionLocal", session_factory)
    monkeypatch.setattr(module, "SchedulerRepository", lambda session: repository)
    app = FastAPI()
    app.include_router(module.router)
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: {"user_id": USER_ID}
    response = TestClient(app).post(path, json={} if operation == "entities" else None)
    if authenticated:
        assert response.status_code == 202
        assert response.json() == ticket
        assert repository.request_run.call_args.kwargs["requested_by"] == USER_ID
        db.execute.assert_not_called()
        db.commit.assert_called_once()
        db.close.assert_called_once()
    else:
        assert response.status_code in (401, 403)
        session_factory.assert_not_called()
        repository.request_run.assert_not_called()


def test_operational_job_manifests_match_workers():
    from job_runtime.requests import REQUEST_ONLY_JOBS

    for job_key, module in {
        **REQUEST_ONLY_JOBS,
        "market_cache_cleanup": "jobs.market_cache_cleanup",
    }.items():
        manifest = json.loads(Path(f"jobs/{job_key}.job.json").read_text(encoding="utf-8"))
        assert manifest["job_key"] == job_key
        assert manifest["python_module"] == module
        assert manifest["enabled_by_default"] is False
        assert Path(module.replace(".", "/") + ".py").is_file()


def test_live_price_frontend_awaits_scheduler_before_polling():
    provider = Path("../mip-frontend/src/contexts/PriceRefreshProvider.tsx").read_text(encoding="utf-8")
    assert 'api.post<ExecutionRequest>("/market-data/prices/refresh"' in provider
    assert "response.data.job_run_id" in provider
    assert "await waitForJobRun" in provider
    assert "if (result.enqueued_count > 0)" in provider
    assert "response.data.enqueued_count" not in provider


@pytest.mark.parametrize("failed", [False, True])
def test_price_refresh_api_only_enqueues_scheduler_request(monkeypatch, failed):
    from routers import market_data

    db = MagicMock()
    repository = MagicMock()
    ticket = {"job_run_id": RUN_ID, "status": "QUEUED"}
    repository.request_run.return_value = ticket
    if failed:
        repository.request_run.side_effect = RuntimeError("queue unavailable")
    monkeypatch.setattr(market_data, "SessionLocal", lambda: db)
    monkeypatch.setattr(market_data, "SchedulerRepository", lambda session: repository)
    if failed:
        with pytest.raises(RuntimeError, match="queue unavailable"):
            market_data.refresh_user_prices({"user_id": USER_ID})
        db.rollback.assert_called_once()
        db.commit.assert_not_called()
    else:
        assert market_data.refresh_user_prices({"user_id": USER_ID}) == ticket
        db.commit.assert_called_once()
    repository.request_run.assert_called_once_with(
        job_key="market_price_refresh_request", requested_by=USER_ID, payload={}, source="PRICE_REFRESH_UI",
    )
    db.execute.assert_not_called()
    db.close.assert_called_once()


@pytest.mark.parametrize("scenario", ["success", "failure", "missing_user"])
def test_price_request_worker_uses_trusted_user(monkeypatch, scenario):
    from job_runtime.requests import current_job_request
    from jobs.market_price_refresh import request

    db = MagicMock()
    session_factory = MagicMock(return_value=db)
    enqueue = MagicMock(return_value={"enqueued_count": 2, "candidates_count": 3})
    if scenario == "failure":
        enqueue.side_effect = RuntimeError("enqueue failed")
    monkeypatch.setattr(request, "SessionLocal", session_factory)
    monkeypatch.setattr(request, "enqueue_user_price_refreshes", enqueue)
    token = current_job_request.set({
        "requested_by": None if scenario == "missing_user" else USER_ID,
        "payload": {"user_id": JOB_ID, "max_age_seconds": 999},
    })
    try:
        if scenario == "missing_user":
            with pytest.raises(ValueError, match="requires a scheduler execution request"):
                request.run()
            session_factory.assert_not_called()
            enqueue.assert_not_called()
            return
        if scenario == "failure":
            with pytest.raises(RuntimeError, match="enqueue failed"):
                request.run()
            db.rollback.assert_called_once()
            db.commit.assert_not_called()
        else:
            assert request.run() == {
                "status": "SUCCESS", "max_age_seconds": 30,
                "enqueued_count": 2, "candidates_count": 3,
            }
            db.commit.assert_called_once()
        enqueue.assert_called_once_with(db, user_id=USER_ID, max_age_seconds=30)
        db.close.assert_called_once()
    finally:
        current_job_request.reset(token)


@pytest.mark.parametrize("failed", [False, True])
def test_cache_cleanup_api_only_requests_execution(monkeypatch, failed):
    import asyncio

    from routers import admin

    db = MagicMock()
    repository = MagicMock()
    ticket = {"job_run_id": RUN_ID, "status": "QUEUED"}
    repository.request_run.return_value = ticket
    error = RuntimeError("queue unavailable")
    if failed:
        repository.request_run.side_effect = error
    monkeypatch.setattr(admin, "SessionLocal", lambda: db)
    monkeypatch.setattr(admin, "SchedulerRepository", lambda session: repository)
    if failed:
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(admin.cleanup_cache({"user_id": USER_ID}))
        assert raised.value is error
        db.rollback.assert_called_once()
        db.commit.assert_not_called()
    else:
        assert asyncio.run(admin.cleanup_cache({"user_id": USER_ID})) == ticket
        db.commit.assert_called_once()
    repository.request_run.assert_called_once_with(
        job_key="market_cache_cleanup", requested_by=USER_ID, payload={}, source="CACHE_UI",
    )
    db.execute.assert_not_called()
    db.close.assert_called_once()


@pytest.mark.parametrize("failed", [False, True])
def test_cache_cleanup_worker_retains_stored_routine(monkeypatch, failed):
    from jobs import market_cache_cleanup

    db = MagicMock()
    db.execute.return_value.scalar.return_value = 7
    error = RuntimeError("cleanup failed")
    if failed:
        db.execute.side_effect = error
    monkeypatch.setattr(market_cache_cleanup, "SessionLocal", lambda: db)
    if failed:
        with pytest.raises(RuntimeError) as raised:
            market_cache_cleanup.run()
        assert raised.value is error
        db.rollback.assert_called_once()
        db.commit.assert_not_called()
    else:
        assert market_cache_cleanup.run() == {"status": "SUCCESS", "deleted": 7}
        db.commit.assert_called_once()
    assert str(db.execute.call_args.args[0]) == "SELECT sp_cleanup_market_cache(30)"
    db.close.assert_called_once()


def test_entity_refresh_api_enqueues_validated_payload(monkeypatch):
    from routers import admin

    db = MagicMock()
    repository = MagicMock()
    ticket = {"job_run_id": RUN_ID, "status": "QUEUED"}
    repository.request_run.return_value = ticket
    monkeypatch.setattr(admin, "SessionLocal", lambda: db)
    monkeypatch.setattr(admin, "SchedulerRepository", lambda session: repository)
    payload = admin.RefreshEntitiesRequest(mode="SYMBOL", symbol="aapl")
    assert admin.refresh_entities_endpoint(payload, {"user_id": USER_ID}) == ticket
    repository.request_run.assert_called_once_with(
        job_key="refresh_entities", requested_by=USER_ID,
        payload={"mode": "SYMBOL", "symbol": "AAPL", "create_if_missing": False, "full_refresh": False},
        source="ENTITIES_UI",
    )
    db.execute.assert_not_called()
    db.commit.assert_called_once()
    db.close.assert_called_once()


def test_entity_refresh_worker_uses_request_options(monkeypatch):
    from job_runtime.requests import current_job_request
    from jobs.entities import refresh_entities
    from services.entities import RefreshMode

    db = MagicMock()
    refresh = MagicMock(return_value={"status": "SUCCESS"})
    monkeypatch.setattr(refresh_entities, "SessionLocal", lambda: db)
    monkeypatch.setattr(refresh_entities, "refresh_entities", refresh)
    token = current_job_request.set({"payload": {
        "mode": "SYMBOL", "symbol": "msft", "create_if_missing": True, "full_refresh": True,
    }})
    try:
        assert refresh_entities.run() == {"status": "SUCCESS"}
    finally:
        current_job_request.reset(token)
    refresh.assert_called_once_with(
        db, mode=RefreshMode.SYMBOL, symbol="MSFT", create_if_missing=True, full_refresh=True,
    )
    db.commit.assert_not_called()
    db.close.assert_called_once()


@pytest.mark.parametrize("job_key, request_only", [
    ("market_screener_context_analysis", True),
    ("market_price_refresh_request", True),
    ("market_screener_pipeline", False),
    ("market_screener_ranking", False),
])
def test_scheduler_job_list_exposes_contextual_execution(monkeypatch, job_key, request_only):
    from routers import system_admin

    db = MagicMock()
    row = {"job_key": job_key, "enabled": False}
    db.execute.return_value.mappings.return_value = [row]
    monkeypatch.setattr(system_admin, "SessionLocal", lambda: db)

    assert system_admin.list_jobs({"user_id": USER_ID}) == [
        {**row, "request_only": request_only},
    ]
    assert "request_only" not in row
    db.close.assert_called_once()


@pytest.mark.parametrize("job_key", ["market_screener_context_analysis", "market_price_refresh_request"])
@pytest.mark.parametrize("operation", ["enable", "run"])
def test_contextual_job_rejects_payloadless_admin_execution(monkeypatch, operation, job_key):
    from routers import system_admin

    session_factory = MagicMock()
    monkeypatch.setattr(system_admin, "SessionLocal", session_factory)
    with pytest.raises(HTTPException) as raised:
        if operation == "enable":
            system_admin.update_job(
                job_key,
                system_admin.JobUpdate(enabled=True),
                current_user={"user_id": USER_ID},
            )
        else:
            system_admin.run_job_now(
                job_key, current_user={"user_id": USER_ID}
            )
    assert raised.value.status_code == 422
    session_factory.assert_not_called()


@pytest.mark.parametrize("module", ["jobs.market_screener_context_analysis.worker", "jobs.market_price_refresh.request"])
def test_contextual_job_is_not_registered_as_recurring(monkeypatch, module):
    import scheduler

    db = MagicMock()
    repository = MagicMock()
    repository.get_enabled_jobs.return_value = [{
        "job_id": JOB_ID,
        "python_module": module,
    }]
    scheduler_instance = MagicMock()
    scheduler_instance.get_jobs.return_value = []
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(scheduler, "scheduler", scheduler_instance)
    scheduler.sync_jobs()
    scheduler_instance.add_job.assert_not_called()
    repository.update_next_run.assert_not_called()
    db.commit.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize("scenario", ["success", "busy", "failed", "cleanup_failed"])
def test_contextual_worker_preserves_lock_and_original_error(monkeypatch, scenario):
    from contextlib import nullcontext

    from job_runtime.requests import current_job_request
    from jobs.market_screener_context_analysis import worker

    db = MagicMock()
    repository = MagicMock()
    error = RuntimeError("scan failed")
    analyze = MagicMock(return_value={"status": "SUCCESS", "run_id": RUN_ID})
    if scenario == "busy":
        analyze.side_effect = ValueError("Analysis already running")
    elif scenario != "success":
        analyze.side_effect = error
    if scenario == "cleanup_failed":
        repository.fail_for_job.side_effect = RuntimeError("cleanup failed")
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "MarketScreenerRunRepository", lambda session: repository)
    monkeypatch.setattr(worker, "hold_incremental_analysis_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(worker, "analyze_screener_context", analyze)
    token = current_job_request.set({
        "requested_by": USER_ID,
        "payload": {"context_type": "WATCHLIST", "context_id": JOB_ID},
    })
    try:
        if scenario == "success":
            assert worker.run() == {"status": "SUCCESS", "run_id": RUN_ID}
            db.commit.assert_called_once()
        elif scenario == "busy":
            assert worker.run() == {"status": "SKIPPED", "reason": "analysis_already_running"}
            repository.fail_for_job.assert_not_called()
        else:
            with pytest.raises(RuntimeError) as raised:
                worker.run()
            assert raised.value is error
            repository.fail_for_job.assert_called_once()
    finally:
        current_job_request.reset(token)
    assert analyze.call_args.kwargs["user_id"] == USER_ID
    assert analyze.call_args.kwargs["incremental"] is True
    db.close.assert_called_once()


def test_portfolio_list_batches_pricing_metadata(monkeypatch):
    db = MagicMock()
    identifiers = [UUID(USER_ID), UUID(JOB_ID)]
    results = [
        SimpleNamespace(_mapping={"portfolio_id": identifier, "portfolio_value": 100})
        for identifier in identifiers
    ]
    metadata = MagicMock()
    metadata.mappings.return_value = [
        {
            "portfolio_id": identifier,
            "total_position_count": 2,
            "pending_price_count": 0,
            "price_refresh_pending": False,
        }
        for identifier in identifiers
    ]
    db.execute.side_effect = [results, metadata]
    monkeypatch.setattr(portfolios, "SessionLocal", lambda: db)
    response = portfolios.get_portfolios(current_user={"user_id": USER_ID})
    assert db.execute.call_count == 2
    assert len(response) == 2
    assert all(row["pricing_complete"] for row in response)
    db.close.assert_called_once()


@pytest.mark.parametrize("amount", ["NaN", "Infinity", -1, "invalid"])
def test_invalid_historical_cost_is_rejected_before_database_access(
    monkeypatch, amount
):
    session_factory = MagicMock()
    monkeypatch.setattr(positions, "SessionLocal", session_factory)
    with pytest.raises(HTTPException) as raised:
        positions.upsert_position(
            {"instrument_id": JOB_ID, "cost_basis_amount": amount},
            current_user={"user_id": USER_ID},
        )
    assert raised.value.status_code == 422
    session_factory.assert_not_called()


def test_inaccessible_watchlist_item_returns_404_and_rolls_back(monkeypatch):
    class Denied(Exception):
        sqlstate = "23503"

    db = MagicMock()
    db.execute.side_effect = DBAPIError(None, None, Denied("inaccessible item"))
    monkeypatch.setattr(watchlist_items, "SessionLocal", lambda: db)
    with pytest.raises(HTTPException) as raised:
        watchlist_items.upsert_watchlist_item(
            {"watchlist_id": JOB_ID, "entity_id": RUN_ID},
            current_user={"user_id": USER_ID},
        )
    assert raised.value.status_code == 404
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()


@pytest.mark.parametrize("expression", ["bad", "60 * * * *", "* * * *", ""])
def test_scheduler_invalid_cron_does_not_open_database(monkeypatch, expression):
    from routers import system_admin

    session_factory = MagicMock()
    monkeypatch.setattr(system_admin, "SessionLocal", session_factory)
    with pytest.raises(HTTPException) as raised:
        system_admin.update_job("job", system_admin.JobUpdate(
            trigger_type="cron", cron_expression=expression,
        ), current_user={"user_id": USER_ID})
    assert raised.value.status_code == 422
    session_factory.assert_not_called()


def test_scheduler_cron_preview_matches_runtime_trigger():
    from datetime import datetime

    from routers import system_admin
    from scheduler_triggers import build_trigger

    expression = "*/15 * * * *"
    preview = system_admin.preview_cron(system_admin.CronPreview(cron_expression=expression))
    next_run = datetime.fromisoformat(preview["next_run_at"])
    trigger = build_trigger({"trigger_type": "cron", "cron_expression": expression})
    assert next_run.minute % 15 == 0
    assert next_run.second == 0
    assert trigger.get_next_fire_time(None, next_run) == next_run
    assert preview["timezone"] == system_admin.SCHEDULER_TIMEZONE


@pytest.mark.parametrize("expression", ["bad", "60 * * * *"])
def test_scheduler_cron_preview_rejects_invalid_expression(expression):
    from routers import system_admin

    with pytest.raises(HTTPException) as raised:
        system_admin.preview_cron(system_admin.CronPreview(cron_expression=expression))
    assert raised.value.status_code == 422


def test_scheduler_contended_manual_run_is_deferred_and_releases_connection(
    monkeypatch,
):
    import scheduler

    db = MagicMock()
    repository = MagicMock()
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = False
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(
        scheduler, "engine", SimpleNamespace(connect=lambda: connection)
    )
    result = scheduler.execute_job(JOB_ID, "unused.module", RUN_ID, True)
    assert result == {"status": "SKIPPED", "reason": "already_running"}
    repository.defer_manual_request.assert_called_once_with(UUID(RUN_ID))
    repository.start_run.assert_not_called()
    db.commit.assert_called_once()
    db.close.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("caller", ["MANUAL", "SCHEDULE", "JOB"])
def test_scheduler_request_context_is_available_and_reset(monkeypatch, fails, caller):
    import scheduler
    from job_runtime.requests import current_job_request

    db = MagicMock()
    repository = MagicMock()
    connection = MagicMock()
    manual_request = caller == "MANUAL"
    request = {"payload": {"context_id": JOB_ID}, "requested_by": USER_ID, "source": "SCREENER_UI"} if manual_request else None
    parent_run_id = UUID(USER_ID) if caller == "JOB" else None
    parent_token = scheduler.current_job_run_id.set(parent_run_id)
    logger = MagicMock()
    monkeypatch.setattr(scheduler, "logger", logger)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(scheduler, "engine", SimpleNamespace(connect=lambda: connection))
    monkeypatch.setattr(scheduler, "_try_job_lock", lambda *args: True)

    def worker():
        assert current_job_request.get() == request
        if fails:
            raise RuntimeError("worker failed")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(
        scheduler.importlib, "import_module", lambda name: SimpleNamespace(run=worker)
    )
    try:
        if fails:
            with pytest.raises(RuntimeError, match="worker failed"):
                scheduler.execute_job(JOB_ID, "worker", RUN_ID, manual_request, request)
        else:
            assert scheduler.execute_job(JOB_ID, "worker", RUN_ID, manual_request, request) == {
                "status": "SUCCESS"
            }
        assert current_job_request.get() is None
        assert scheduler.current_job_run_id.get() == parent_run_id
        start_log = logger.info.call_args_list[0].args
        assert "caller=%s" in start_log[0]
        assert start_log[4:] == (
            caller,
            "SCREENER_UI" if manual_request else caller,
            USER_ID if manual_request else "-",
            parent_run_id or "-",
        )
    finally:
        scheduler.current_job_run_id.reset(parent_token)


@pytest.mark.parametrize("manual_request", [False, True])
def test_scheduler_skipped_worker_preserves_result_without_failure(
    monkeypatch, manual_request
):
    import scheduler

    db = MagicMock()
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = True
    result = {"status": "SKIPPED", "reason": "analysis_already_running"}
    worker = MagicMock(return_value=result)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        scheduler, "engine", SimpleNamespace(connect=lambda: connection)
    )
    monkeypatch.setattr(
        scheduler.importlib,
        "import_module",
        lambda name: SimpleNamespace(run=worker),
    )

    assert scheduler.execute_job(
        JOB_ID, "jobs.market_screener_pipeline.worker", RUN_ID, manual_request
    ) == result

    completion = db.execute.call_args_list[1]
    assert "sp_complete_scheduler_job" in str(completion.args[0])
    parameters = completion.args[1]
    assert parameters["status"] == "SUCCESS"
    assert parameters["error"] is None
    assert json.loads(parameters["result"]) == result
    assert db.execute.call_count == (3 if manual_request else 2)
    if manual_request:
        assert "sp_scheduler_complete_job_run_request" in str(
            db.execute.call_args_list[2].args[0]
        )
    worker.assert_called_once_with()
    db.rollback.assert_not_called()
    assert db.commit.call_count == 2
    db.close.assert_called_once()
    connection.close.assert_called_once()


def test_scheduler_context_request_requeues_shared_lock_contention(monkeypatch):
    import scheduler

    db = MagicMock()
    repository = MagicMock()
    connection = MagicMock()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(scheduler, "engine", SimpleNamespace(connect=lambda: connection))
    monkeypatch.setattr(scheduler, "_try_job_lock", lambda *args: True)
    monkeypatch.setattr(scheduler.importlib, "import_module", lambda name: SimpleNamespace(
        run=lambda: {"status": "SKIPPED", "reason": "analysis_already_running"}
    ))
    scheduler.execute_job(JOB_ID, "worker", RUN_ID, True, {"requested_by": USER_ID})
    repository.defer_manual_request.assert_called_once_with(UUID(RUN_ID))
    repository.complete_manual_request.assert_not_called()


@pytest.mark.parametrize("found", [False, True])
def test_job_run_status_is_scoped_to_requesting_user(monkeypatch, found):
    from routers import job_runs

    db = MagicMock()
    repository = MagicMock()
    ticket = {"job_run_id": RUN_ID, "status": "QUEUED"}
    repository.request_status.return_value = ticket if found else None
    monkeypatch.setattr(job_runs, "SessionLocal", lambda: db)
    monkeypatch.setattr(job_runs, "SchedulerRepository", lambda session: repository)
    if found:
        assert job_runs.execution_status(UUID(RUN_ID), {"user_id": USER_ID}) == ticket
    else:
        with pytest.raises(HTTPException) as raised:
            job_runs.execution_status(UUID(RUN_ID), {"user_id": USER_ID})
        assert raised.value.status_code == 404
    repository.request_status.assert_called_once_with(job_run_id=RUN_ID, user_id=USER_ID)
    db.close.assert_called_once()
