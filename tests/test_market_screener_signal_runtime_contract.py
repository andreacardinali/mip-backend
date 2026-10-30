import ast
import re
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest


def test_snapshot_changes_installer_compares_successful_unfiltered_snapshots() -> None:
    sql = Path("oneshot/install_screener_snapshot_changes.sql").read_text(
        encoding="utf-8"
    )
    assert sql.count("r.status = 'SUCCESS'") == 2
    assert sql.count("r.phase_results->'analysis_scan'->>'status' = 'SUCCESS'") == 2
    assert sql.count("r.phase_results->'ranking'->>'status' = 'SUCCESS'") == 2
    assert "FULL JOIN previous_items" in sql
    assert "current.instrument_id = previous.instrument_id" in sql
    assert "IS DISTINCT FROM" in sql
    assert "'NEW'" in sql and "'CHANGED'" in sql and "'REMOVED'" in sql
    comparison = sql.split("OR ROW(")[1].split("SELECT jsonb_build_object")[0]
    assert "reference_price" not in comparison
    assert "composite_score" not in comparison
    assert "ranking_position" not in comparison
    assert "previous_snapshot_id" in sql


def test_performance_installer_preserves_asof_prices_and_direction_cycles() -> None:
    sql = Path("oneshot/install_signal_performance_history.sql").read_text(
        encoding="utf-8"
    )
    assert "BEFORE INSERT ON public.market_analysis_signal_events" in sql
    assert (
        "performance_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()" in sql
    )
    assert "COALESCE((payload->>'processed_at')::timestamptz, created_at)" in sql
    assert (
        "ORDER BY next_event.performance_recorded_at, next_event.signal_event_id" in sql
    )
    assert "WHERE NOT payload ? 'performance'" in sql
    assert "instrument_id, observed_at, payload, false" in sql
    assert "ts <= p_observed_at" in sql
    assert "observed_at <= p_observed_at" in sql
    assert "'CREATED', 'DIRECTION_CHANGED', 'CLOSED', 'EXPIRED'" in sql
    assert "new_direction IN ('LONG', 'SHORT')" in sql
    assert "CASE WHEN new_direction = 'SHORT' THEN -1 ELSE 1 END" in sql
    assert "(valuation->>'price_at')::timestamptz >= observed_at" in sql
    assert "exit_at >= observed_at" in sql
    assert "OFFSET GREATEST(p_offset, 0)" in sql


def test_api_enriches_existing_opportunity_payloads() -> None:
    repository = Path("market_screener/api_repository.py").read_text(encoding="utf-8")
    assert repository.count("sp_market_screener_enrich_opportunity_payload") == 2
    assert "sp_market_screener_list_user_opportunities" in repository
    assert "sp_market_screener_get_user_opportunity" in repository


def test_gui_handles_missing_signal_arrays() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "selected.signal_reasons ?? []" in page


def test_gui_exports_authorized_results_with_native_grid_csv() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    grids = [
        options
        for options in re.findall(r"<DataGrid\b(.*?)/>", page, re.DOTALL)
        if re.search(r"\brows\s*=\s*\{rows\}", options)
    ]
    assert len(grids) == 1, "Expected one grid for authorized screener results"
    options = grids[0]
    assert re.search(r"\brows\s*=\s*\{rows\}", options)
    assert re.search(r"\bshowToolbar\b", options)
    assert re.search(r'fileName\s*:\s*"mip-screener"', options)
    assert re.search(r"utf8WithBom\s*:\s*true", options)
    assert re.search(r"escapeFormulas\s*:\s*true", options)
    assert re.search(r"disableToolbarButton\s*:\s*true", options)
    assert "createObjectURL" not in page


@pytest.mark.parametrize("outcome", ["success", "missing", "failure"])
def test_signal_history_uses_owner_scoped_source_instrument(outcome) -> None:
    tree = ast.parse(Path("routers/market_screener.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "list_screener_signal_events"
    )
    assert ast.unparse(function.decorator_list[0]) == (
        "router.get('/{opportunity_id}/signal-events')"
    )
    function.decorator_list = []
    calls = []
    session = SimpleNamespace(close=lambda: calls.append(("close",)))
    opportunity_id = UUID("00000000-0000-0000-0000-000000000001")
    events = [{"event_type": "DIRECTION_CHANGED"}]

    class RouteError(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            super().__init__(detail)

    def get_opportunity(**kwargs):
        calls.append(("opportunity", kwargs))
        if outcome == "missing":
            return None
        return {"source_instrument_id": "source", "instrument_id": "equivalent"}

    def list_events(**kwargs):
        calls.append(("events", kwargs))
        if outcome == "failure":
            raise RuntimeError("database unavailable")
        return events

    namespace = {
        "UUID": UUID,
        "CurrentUser": dict,
        "Query": lambda default, **kwargs: default,
        "Depends": lambda dependency: None,
        "get_current_user": lambda: None,
        "HTTPException": RouteError,
        "SessionLocal": lambda: session,
        "MarketScreenerApiRepository": lambda db: SimpleNamespace(
            get_opportunity=get_opportunity
        ),
        "SignalLifecycleRepository": lambda db: SimpleNamespace(
            list_events=list_events
        ),
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "route", "exec"),
        namespace,
    )
    route = namespace["list_screener_signal_events"]
    if outcome == "missing":
        with pytest.raises(RouteError) as error:
            route(opportunity_id, limit=20, current_user={"user_id": "owner"})
        assert error.value.status_code == 404
        assert not any(call[0] == "events" for call in calls)
    elif outcome == "failure":
        with pytest.raises(RuntimeError, match="database unavailable"):
            route(opportunity_id, limit=20, current_user={"user_id": "owner"})
    else:
        assert (
            route(opportunity_id, limit=20, current_user={"user_id": "owner"}) == events
        )
    assert calls[0] == (
        "opportunity",
        {"user_id": "owner", "opportunity_id": str(opportunity_id)},
    )
    if outcome != "missing":
        assert calls[1] == ("events", {"instrument_id": "source", "limit": 20})
    assert calls[-1] == ("close",)


def test_signal_history_repository_reuses_stored_routine() -> None:
    repository = runpy.run_path("market_analysis/lifecycle_repository.py")[
        "SignalLifecycleRepository"
    ]
    calls = []
    events = [{"signal_event_id": "event", "event_type": "CREATED"}]

    def execute(statement, params):
        calls.append((str(statement), params))
        return SimpleNamespace(scalars=lambda: iter(events))

    assert (
        repository(SimpleNamespace(execute=execute)).list_events(
            instrument_id="source", limit=20
        )
        == events
    )
    assert "public.sp_market_analysis_list_signal_events(" in calls[0][0]
    assert calls[0][1] == {"instrument": "source", "strategy": None, "limit": 20}


def test_performance_repository_reuses_stored_routine() -> None:
    repository = runpy.run_path("market_analysis/lifecycle_repository.py")[
        "SignalLifecycleRepository"
    ]
    calls = []
    rows = [{"performance_id": "cycle", "return_pct": None}]

    def execute(statement, params):
        calls.append((str(statement), params))
        return SimpleNamespace(scalars=lambda: iter(rows))

    assert (
        repository(SimpleNamespace(execute=execute)).list_performance(
            instrument_id="source",
            strategy_code="SWING",
            status="CLOSED",
            limit=25,
            offset=50,
        )
        == rows
    )
    assert "public.sp_market_analysis_list_signal_performance(" in calls[0][0]
    assert calls[0][1] == {
        "instrument": "source",
        "strategy": "SWING",
        "status": "CLOSED",
        "limit": 25,
        "offset": 50,
    }


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("success", 200),
        ("failure", 500),
        ("unauthenticated", 401),
        ("invalid_id", 422),
        ("invalid_status", 422),
        ("negative_offset", 422),
        ("high_limit", 422),
    ],
)
def test_performance_http_contract(monkeypatch, scenario, expected_status) -> None:
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    calls = []

    def current_user():
        if scenario == "unauthenticated":
            raise fastapi.HTTPException(401, "Authentication required")
        return {"user_id": "owner"}

    def list_performance(**kwargs):
        calls.append(kwargs)
        if scenario == "failure":
            raise RuntimeError("database unavailable")
        return [{"performance_id": "cycle", "return_pct": None}]

    modules = {
        "auth.security": {"CurrentUser": dict, "get_current_user": current_user},
        "db": {
            "SessionLocal": lambda: SimpleNamespace(close=lambda: calls.append("close"))
        },
        "market_screener.api_repository": {
            "MarketScreenerApiRepository": lambda db: None
        },
        "market_analysis.lifecycle_repository": {
            "SignalLifecycleRepository": lambda db: SimpleNamespace(
                list_performance=list_performance
            )
        },
    }
    with monkeypatch.context() as patch:
        for name, attributes in modules.items():
            module = ModuleType(name)
            module.__dict__.update(attributes)
            patch.setitem(sys.modules, name, module)
        router = runpy.run_path("routers/market_screener.py")["router"]
    app = fastapi.FastAPI()
    app.include_router(router, prefix="/api/screener")
    params = {"strategy_code": "swing", "status": "CLOSED", "limit": 25, "offset": 50}
    invalid = {
        "invalid_id": ("instrument_id", "invalid"),
        "invalid_status": ("status", "INVALID"),
        "negative_offset": ("offset", -1),
        "high_limit": ("limit", 1001),
    }
    if scenario in invalid:
        key, value = invalid[scenario]
        params[key] = value
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/screener/performance", params=params)
    assert response.status_code == expected_status
    if scenario in ("success", "failure"):
        assert calls == [
            {
                "instrument_id": None,
                "strategy_code": "SWING",
                "status": "CLOSED",
                "limit": 25,
                "offset": 50,
            },
            "close",
        ]
    else:
        assert calls == []
    if scenario == "success":
        assert response.json() == [{"performance_id": "cycle", "return_pct": None}]


@pytest.mark.parametrize("source_format", ["original", "multiline", "single_quotes"])
def test_gui_loads_signal_history_only_for_selected_detail(source_format) -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    service = Path("../mip-frontend/src/services/marketScreener.ts").read_text(
        encoding="utf-8"
    )
    query = re.search(
        r"const\s+signalEventsQuery\s*=\s*useQuery\s*\(\s*\{(.*?)\}\s*\)",
        page,
        re.DOTALL,
    )
    assert query is not None, "Signal history query is missing from ScreenerPage"
    options = query.group(1)
    if source_format == "multiline":
        options = re.sub(r"\s+", "\n", options)
        options = re.sub(r"selectedId\s*\]", "selectedId,\n]", options)
    elif source_format == "single_quotes":
        options = options.replace('"', "'")
    assert re.search(
        r"""queryKey\s*:\s*\[\s*['"]market-screener-signal-events['"]\s*,\s*selectedId\s*,?\s*\]""",
        options,
    )
    assert re.search(
        r"""queryFn\s*:\s*\(\s*\)\s*=>\s*getScreenerSignalEvents\s*\(\s*selectedId\s*\?\?\s*(?:""|'')\s*\)""",
        options,
    )
    assert re.search(
        r"enabled\s*:\s*Boolean\s*\(\s*selectedId\s*&&\s*detailQuery\s*\.\s*data\s*\)",
        options,
    )
    assert "row.signal_event_id" in page
    assert "/screener/${id}/signal-events" in service


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("success", 200),
        ("missing", 404),
        ("failure", 500),
        ("unauthenticated", 401),
        ("invalid_id", 422),
        ("low_limit", 422),
        ("high_limit", 422),
    ],
)
def test_signal_history_http_contract(monkeypatch, scenario, expected_status) -> None:
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    calls = []
    events = [{"signal_event_id": "event", "event_type": "CREATED"}]

    def current_user():
        if scenario == "unauthenticated":
            raise fastapi.HTTPException(401, "Authentication required")
        return {"user_id": "owner"}

    def session_factory():
        calls.append("session")
        return SimpleNamespace(close=lambda: calls.append("close"))

    def get_opportunity(**kwargs):
        assert kwargs["user_id"] == "owner"
        calls.append("opportunity")
        return None if scenario == "missing" else {"source_instrument_id": "source"}

    def list_events(**kwargs):
        assert kwargs == {"instrument_id": "source", "limit": 20}
        calls.append("events")
        if scenario == "failure":
            raise RuntimeError("database unavailable")
        return events

    modules = {
        "auth.security": {"CurrentUser": dict, "get_current_user": current_user},
        "db": {"SessionLocal": session_factory},
        "market_screener.api_repository": {
            "MarketScreenerApiRepository": lambda db: SimpleNamespace(
                get_opportunity=get_opportunity
            )
        },
        "market_analysis.lifecycle_repository": {
            "SignalLifecycleRepository": lambda db: SimpleNamespace(
                list_events=list_events
            )
        },
    }
    with monkeypatch.context() as patch:
        for name, attributes in modules.items():
            module = ModuleType(name)
            module.__dict__.update(attributes)
            patch.setitem(sys.modules, name, module)
        router = runpy.run_path("routers/market_screener.py")["router"]
    app = fastapi.FastAPI()
    app.include_router(router, prefix="/api/screener")
    opportunity_id = (
        "invalid"
        if scenario == "invalid_id"
        else ("00000000-0000-0000-0000-000000000001")
    )
    limit = 0 if scenario == "low_limit" else 1001 if scenario == "high_limit" else 20
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/screener/{opportunity_id}/signal-events", params={"limit": limit}
        )
    assert response.status_code == expected_status
    if scenario == "success":
        assert response.json() == events
    if expected_status in (401, 422):
        assert calls == []
    else:
        assert calls[-1] == "close"
    if scenario == "missing":
        assert "events" not in calls
