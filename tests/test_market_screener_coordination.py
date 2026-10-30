import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("scenario", ["busy", "success", "nested", "failure"])
def test_analysis_lock_lifetime_and_nested_phases(scenario):
    tree = ast.parse(Path("market_screener/coordination.py").read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or node.module not in {"db", "market_screener.run_repository"}
    ]
    events = []
    db = SimpleNamespace(
        rollback=lambda: events.append("rollback"), close=lambda: events.append("close")
    )
    namespace = {
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


@pytest.mark.parametrize("scenario", ["success", "failure", "busy"])
def test_incremental_lock_pins_connection_and_releases_after_commits(scenario):
    tree = ast.parse(Path("market_screener/coordination.py").read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or node.module not in {"db", "market_screener.run_repository"}
    ]
    events = []
    from contextlib import nullcontext

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
        "MarketScreenerRunRepository": lambda db: SimpleNamespace(
            session_analysis_lock=lock
        )
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
    assert events == (
        ["lock", "rollback"]
        if scenario == "busy"
        else ["lock", "commit", "rollback", "unlock", "commit"]
    )
