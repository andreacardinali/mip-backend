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
