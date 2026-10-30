import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("scenario", ["busy", "success", "nested", "failure"])
def test_analysis_lock_lifetime_and_nested_phases(scenario):
    tree = ast.parse(Path("market_screener/coordination.py").read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or node.module
        not in {"db", "market_screener.run_repository", "job_runtime.lock_logging"}
    ]
    events = []
    db = SimpleNamespace(
        rollback=lambda: events.append("rollback"),
        close=lambda: events.append("close"),
        connection=lambda: "connection",
    )
    namespace = {
        "analysis_lock_owner": lambda operation: operation,
        "lock_identity": lambda *args, **kwargs: nullcontext(),
        "log_lock_contention": MagicMock(),
        "SessionLocal": lambda: events.append("open") or db,
        "MarketScreenerRunRepository": lambda db: SimpleNamespace(
            try_analysis_lock=lambda: events.append("lock") or scenario != "busy"
        ),
    }
    exec(compile(tree, "coordination.py", "exec"), namespace)
    run = namespace["run_coordinated_analysis"]

    def operation():
        events.append("operation")
        if scenario == "failure":
            raise RuntimeError("phase failed")
        if scenario == "nested":
            return run(lambda: {"status": "SUCCESS"})
        return {"status": "SUCCESS"}

    if scenario == "failure":
        with pytest.raises(RuntimeError, match="phase failed"):
            run(operation)
    else:
        result = run(operation)
        assert result["status"] == ("SKIPPED" if scenario == "busy" else "SUCCESS")
    assert events == ["open", "lock"] + (
        [] if scenario == "busy" else ["operation"]
    ) + ["rollback", "close"]
    assert namespace["_analysis_locked"].get() is False
    assert namespace["log_lock_contention"].call_count == (scenario == "busy")


@pytest.mark.parametrize("scenario", ["success", "failure", "busy"])
def test_incremental_lock_pins_connection_and_releases_after_commits(scenario):
    tree = ast.parse(Path("market_screener/coordination.py").read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or node.module
        not in {"db", "market_screener.run_repository", "job_runtime.lock_logging"}
    ]
    events = []
    connection = SimpleNamespace(invalidate=lambda: events.append("invalidate"))
    engine = SimpleNamespace(connect=lambda: nullcontext(connection))
    db = SimpleNamespace(
        bind=engine,
        get_bind=lambda: engine,
        rollback=lambda: events.append("rollback"),
        commit=lambda: events.append("commit"),
    )

    def lock(*, release=False):
        assert db.bind is connection
        events.append("unlock" if release else "lock")
        return scenario != "busy"

    namespace = {
        "analysis_lock_owner": lambda operation: operation,
        "lock_identity": lambda *args, **kwargs: nullcontext(),
        "log_lock_contention": MagicMock(),
        "MarketScreenerRunRepository": lambda db: SimpleNamespace(
            session_analysis_lock=lock
        ),
    }
    exec(compile(tree, "coordination.py", "exec"), namespace)

    def operation():
        with namespace["hold_incremental_analysis_lock"](db):
            db.commit()
            assert db.bind is connection
            if scenario == "failure":
                raise RuntimeError("interrupted")

    if scenario == "success":
        operation()
    else:
        with pytest.raises(ValueError if scenario == "busy" else RuntimeError):
            operation()
    assert db.bind is engine
    assert namespace["log_lock_contention"].call_count == (scenario == "busy")
    assert events == (
        ["lock", "rollback"]
        if scenario == "busy"
        else ["lock", "commit", "rollback", "unlock", "commit"]
    )


@pytest.mark.parametrize("scheduler", [False, True])
def test_lock_contention_logs_holder_identity_and_activity(caplog, scheduler):
    from job_runtime.lock_logging import log_lock_contention

    connection = MagicMock()
    holder = {
        "pid": 123,
        "owner": "mip:run:running",
        "run_id": "running",
        "job_id": "job",
        "module": "jobs.market_analysis_scan.worker",
        "state": "idle in transaction",
        "wait_event": "ClientRead",
    }
    connection.execute.return_value.mappings.return_value.all.return_value = [holder]
    log_lock_contention(
        connection, lock_name="test_lock", owner="requester", scheduler=scheduler
    )
    assert "Lock contention" in caplog.text
    assert "jobs.market_analysis_scan.worker" in caplog.text
    assert "123" in caplog.text
    assert "requester" in caplog.text
    assert connection.execute.call_args.args[1] == {
        "lock_name": "test_lock",
        "scheduler": scheduler,
    }
    connection.rollback.assert_not_called()


def test_lock_diagnostics_failure_does_not_fail_job(caplog):
    from job_runtime.lock_logging import lock_identity, log_lock_contention

    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("monitoring unavailable")
    with lock_identity(connection, "requester"):
        log_lock_contention(connection, lock_name="test_lock", owner="requester")
    assert "blockers=unavailable" in caplog.text
    connection.rollback.assert_not_called()
    connection.invalidate.assert_not_called()


@pytest.mark.parametrize("session_scoped", [False, True])
def test_lock_identity_restores_session_label_before_pool_reuse(session_scoped):
    from job_runtime.lock_logging import lock_identity

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = "original"
    with lock_identity(connection, "mip:gui:watchlist", session_scoped=session_scoped):
        assert connection.execute.call_args.args[1] == {
            "owner": "mip:gui:watchlist",
            "local": not session_scoped,
        }
    if session_scoped:
        assert connection.execute.call_args.args[1] == {"owner": "original"}
        connection.commit.assert_called_once()
    else:
        connection.commit.assert_not_called()


def test_scheduler_contention_logs_actual_holder(monkeypatch, caplog):
    import scheduler

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = False
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"pid": 123, "job_id": "blocking-job", "run_id": "blocking-run"}
    ]
    monkeypatch.setattr(scheduler, "SessionLocal", MagicMock())
    monkeypatch.setattr(
        scheduler, "engine", SimpleNamespace(connect=lambda: connection)
    )
    result = scheduler.execute_job(
        "00000000-0000-0000-0000-000000000001", "unused.module"
    )
    assert result == {"status": "SKIPPED", "reason": "already_running"}
    assert "blocking-job" in caplog.text
    assert "blocking-run" in caplog.text
    connection.close.assert_called_once()
