import ast
import re
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "sp_get_user_notification_preferences",
        "sp_upsert_user_notification_preference",
        "sp_market_screener_summary_recipients",
    ],
)
def test_screener_summary_preference_is_opt_in_and_matches_schema(name) -> None:
    installer = Path("oneshot/install_screener_summary_notifications.sql").read_text(
        encoding="utf-8"
    )
    schema = Path("mip_schema.sql").read_text(encoding="utf-8")
    pattern = rf"FUNCTION public\.{name}\(.*?\$\$;"
    installed = re.search(pattern, installer, re.DOTALL)
    snapshot = re.search(pattern, schema, re.DOTALL)
    assert installed is not None and snapshot is not None
    installed_body = installed.group().split("$$", 1)[1]
    snapshot_body = snapshot.group().split("$$", 1)[1]
    assert " ".join(installed_body.split()) == " ".join(snapshot_body.split())
    assert "'SCREENER_SUMMARY'" in installed_body
    if name == "sp_get_user_notification_preferences":
        assert re.search(
            r"'SCREENER_SUMMARY'.*?::text, false\)", installed_body, re.DOTALL
        )
    elif name == "sp_market_screener_summary_recipients":
        assert "ns.portfolio_id IS NULL" in installed_body
        assert "ns.enabled = true" in installed_body


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_reason"),
    [
        ("disabled", "DISABLED", None),
        ("incomplete", "SKIPPED", "incomplete_snapshot"),
        ("initial", "SKIPPED", "initial_snapshot"),
        ("unchanged", "SKIPPED", "no_changes"),
        ("changed", "SUCCESS", None),
    ],
)
def test_snapshot_summary_reuses_comparison_and_queue(
    monkeypatch, scenario, expected_status, expected_reason
) -> None:
    namespace = runpy.run_path("market_screener/service.py")
    enqueue = namespace["enqueue_snapshot_summary"]
    calls = []
    comparison = {
        "ranking_snapshot_id": "snapshot",
        "previous_snapshot_id": None if scenario == "initial" else "previous",
        "new_count": 0 if scenario == "unchanged" else 2,
        "changed_count": 0 if scenario == "unchanged" else 1,
        "removed_count": 0 if scenario == "unchanged" else 3,
        "changes": [{"private_detail": "not exported"}],
    }

    def get_changes(snapshot_id):
        calls.append(("comparison", snapshot_id))
        return None if scenario == "incomplete" else comparison

    def recipients():
        calls.append("recipients")
        return ["owner", "other"]

    def queue(**payload):
        calls.append(payload)
        return SimpleNamespace(
            status="CREATED" if payload["user_id"] == "owner" else "DUPLICATE"
        )

    monkeypatch.setitem(
        enqueue.__globals__,
        "MarketScreenerApiRepository",
        lambda db: SimpleNamespace(
            get_snapshot_changes=get_changes, list_summary_recipients=recipients
        ),
    )
    monkeypatch.setitem(
        enqueue.__globals__,
        "NotificationQueue",
        lambda db, **kwargs: SimpleNamespace(enqueue=queue),
    )
    result = enqueue(None, snapshot_id="snapshot", enabled=scenario != "disabled")
    assert result["status"] == expected_status
    if expected_reason:
        assert result["reason"] == expected_reason
    if scenario == "disabled":
        assert calls == []
    elif scenario != "changed":
        assert calls == [("comparison", "snapshot")]
    else:
        assert result["outcomes"] == {"CREATED": 1, "DUPLICATE": 1}
        assert calls[:2] == [("comparison", "snapshot"), "recipients"]
        for payload, user_id in zip(calls[2:], ["owner", "other"], strict=True):
            assert payload["notification_key"] == f"SCREENER_SUMMARY:{user_id}:snapshot"
            assert payload["notification_type"] == "SCREENER_SUMMARY"
            assert payload["severity"] == "LOW"
            assert payload["entity_id"] is None and payload["symbol"] is None
            assert payload["context"] == {
                "ranking_snapshot_id": "snapshot",
                "previous_snapshot_id": "previous",
                "new_count": 2,
                "changed_count": 1,
                "removed_count": 3,
            }
            assert payload["message"] == (
                "Nuove entrate: 2\nCambi del segnale: 1\nUscite: 3"
            )


def test_summary_recipient_repository_calls_only_stored_routine() -> None:
    repository = runpy.run_path("market_screener/api_repository.py")[
        "MarketScreenerApiRepository"
    ]
    calls = []

    def execute(statement):
        calls.append(str(statement))
        return SimpleNamespace(scalars=lambda: iter([UUID(int=1), UUID(int=2)]))

    assert repository(SimpleNamespace(execute=execute)).list_summary_recipients() == [
        str(UUID(int=1)),
        str(UUID(int=2)),
    ]
    assert calls == [
        "SELECT user_id FROM public.sp_market_screener_summary_recipients()"
    ]


@pytest.mark.parametrize("watchlist_match", [None, False, True])
def test_opportunity_filters_and_limit_are_applied_by_sql(watchlist_match) -> None:
    repository = runpy.run_path("market_screener/api_repository.py")[
        "MarketScreenerApiRepository"
    ]
    calls = []
    results = [{"opportunity_id": "late-match", "consensus_direction": "BULLISH"}]

    def execute(statement, params):
        calls.append((str(statement), params))
        return SimpleNamespace(scalars=lambda: iter(results))

    assert (
        repository(SimpleNamespace(execute=execute)).list_opportunities(
            user_id="owner",
            watchlist_match=watchlist_match,
            consensus_direction="BULLISH",
            strategy_code="SUPERTREND",
            context_type="WATCHLIST",
            context_id="watchlist",
            limit=1,
        )
        == results
    )
    statement, params = calls[0]
    assert "sp_market_screener_list_filtered_opportunities" in statement
    assert params["limit"] == 1
    assert "fetch_limit" not in params
    assert params["consensus_direction"] == "BULLISH"
    assert params["strategy_code"] == "SUPERTREND"
    assert params["context_type"] == "WATCHLIST"
    assert params["context_id"] == "watchlist"
    assert params["watchlist_match"] is watchlist_match
    assert ("CAST(:watchlist_match AS boolean)" in statement) == (
        watchlist_match is not None
    )


def test_watchlist_filter_installer_matches_schema_and_filters_before_limit() -> None:
    schema = Path("mip_schema.sql").read_text(encoding="utf-8")
    installer = Path("oneshot/install_screener_watchlist_filter.sql").read_text(
        encoding="utf-8"
    )
    pattern = r"FUNCTION public\.sp_market_screener_list_filtered_opportunities\([^\n]*p_watchlist_match boolean\).*?AS\s+\$\$(.*?)\$\$;"
    bodies = [
        re.search(pattern, sql, re.DOTALL).group(1) for sql in (schema, installer)
    ]
    assert bodies[0] == bodies[1]
    assert (
        "sp_get_user_entity_asset_match(p_user_id, coalesce(o.entity_id, i.entity_id), o.instrument_id)"
        in bodies[0]
    )
    assert bodies[0].index("p_watchlist_match IS NULL") < bodies[0].index("LIMIT least")
    assert "2147483647" in bodies[0]
    assert "->> 'in_watchlist'" in bodies[0]


@pytest.mark.parametrize(
    "scenario",
    ["success", "partial", "scan_disabled", "ranking_disabled", "queue_failure"],
)
def test_pipeline_summary_is_post_commit_and_cannot_fail_snapshot(scenario) -> None:
    tree = ast.parse(Path("jobs/market_screener_pipeline/worker.py").read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("run", "_run_pipeline", "_queue_snapshot_summary")
    ]
    calls = []
    session = SimpleNamespace(
        commit=lambda: calls.append("commit"),
        close=lambda: calls.append("close"),
        rollback=lambda: calls.append("unlock"),
    )
    repository = SimpleNamespace(
        try_analysis_lock=lambda: True,
        start=lambda: "run",
        create_snapshot=lambda **kwargs: "snapshot",
        complete=lambda **kwargs: calls.append(("complete", kwargs["status"])),
    )

    def transaction(operation):
        calls.append("summary_transaction")
        if scenario == "queue_failure":
            raise RuntimeError("queue unavailable")
        return operation(session)

    def enqueue(db, **kwargs):
        calls.append(("enqueue", kwargs))
        return {"status": "SUCCESS"}

    namespace = {
        "Any": object,
        "SessionLocal": lambda: session,
        "MarketScreenerRunRepository": lambda db: repository,
        "logger": SimpleNamespace(
            info=lambda *args, **kwargs: None, exception=lambda *args: None
        ),
        "_enabled": lambda name, default=True: name
        not in {
            (
                "MARKET_SCREENER_PIPELINE_SCAN_ENABLED"
                if scenario == "scan_disabled"
                else ""
            ),
            (
                "MARKET_SCREENER_PIPELINE_RANKING_ENABLED"
                if scenario == "ranking_disabled"
                else ""
            ),
        },
        "_run_phase": lambda name, operation: operation(),
        "run_discovery_refresh": lambda: {"status": "SUCCESS"},
        "run_candidate_resolver": lambda: {"status": "SUCCESS"},
        "run_analysis_scan": lambda: {
            "status": "PARTIAL" if scenario == "partial" else "SUCCESS"
        },
        "run_llm_enrichment": lambda: {"status": "SUCCESS"},
        "run_ranking": lambda: {"status": "SUCCESS"},
        "NotificationConfig": SimpleNamespace(
            load=lambda: SimpleNamespace(enabled=True)
        ),
        "run_transactional": transaction,
        "run_coordinated_analysis": lambda operation: operation(),
        "enqueue_snapshot_summary": enqueue,
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "worker", "exec"),
        namespace,
    )
    result = namespace["run"]()
    assert result["status"] == ("PARTIAL" if scenario == "partial" else "SUCCESS")
    assert result["ranking_snapshot_id"] == "snapshot"
    assert ("complete", "FAILED") not in calls
    if scenario in ("partial", "scan_disabled", "ranking_disabled"):
        assert result["notification_summary"]["status"] == "SKIPPED"
        assert "summary_transaction" not in calls
    else:
        assert calls[:5] == [
            "commit",
            "close",
            ("complete", "SUCCESS"),
            "commit",
            "close",
        ]
        assert calls[5] == "summary_transaction"
        assert result["notification_summary"]["status"] == (
            "FAILED" if scenario == "queue_failure" else "SUCCESS"
        )


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
    assert repository.count("sp_market_screener_enrich_opportunity_payload") == 1
    assert "sp_market_screener_list_filtered_opportunities" in repository
    assert "sp_market_screener_get_user_opportunity" in repository


def test_context_installer_matches_schema_and_filters_before_limit() -> None:
    import re

    schema = Path("mip_schema.sql").read_text(encoding="utf-8")
    installer = Path("oneshot/install_screener_contexts.sql").read_text(
        encoding="utf-8"
    )
    for name in (
        "sp_market_screener_context_instruments",
        "sp_market_screener_list_filtered_opportunities",
        "sp_market_screener_list_contexts",
        "sp_market_screener_prepare_context_scan",
        "sp_market_screener_try_analysis_lock",
    ):
        pattern = rf"FUNCTION public\.{name}\(.*?AS\s+\$\$(.*?)\$\$;"
        bodies = [
            re.search(pattern, sql, re.DOTALL).group(1) for sql in (schema, installer)
        ]
        assert [
            re.sub(r"\s+", " ", body.replace("character varying", "varchar")).strip()
            for body in bodies
        ][0] == [
            re.sub(r"\s+", " ", body.replace("character varying", "varchar")).strip()
            for body in bodies
        ][
            1
        ]
    assert "w.user_id = p_user_id" in installer
    assert "p.user_id = p_user_id" in installer
    assert "pos.quantity <> 0" in installer
    assert "mum.valid_to IS NULL" in installer
    assert (
        "CREATE UNIQUE INDEX uq_market_universes_code ON public.market_universes "
        "USING btree (upper(btrim((code)::text)));"
    ) in schema
    assert "ON CONFLICT (upper(btrim(code::text))) DO UPDATE" in installer
    assert "ON CONFLICT (code)" not in installer
    assert installer.index(
        "o.consensus_direction = p_consensus_direction"
    ) < installer.index("LIMIT least")


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "partial",
        "empty",
        "too_many",
        "failure",
        "ai_success",
        "ai_partial",
        "ai_failure",
    ],
)
def test_manual_context_scan_records_history_in_callers_transaction(
    monkeypatch, outcome
):
    calls = []
    context = {"user_id": "owner", "context_type": "WATCHLIST", "context_id": "list"}
    member = {
        "instrument_id": "instrument",
        "entity_id": "entity",
        "symbol": "ABC",
        "exchange": "X",
        "currency_code": "USD",
    }
    count = 0 if outcome == "empty" else 1001 if outcome == "too_many" else 1
    savepoints = []
    db = SimpleNamespace(begin_nested=lambda: savepoints.append(True) or nullcontext())
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    snapshot_id = UUID("00000000-0000-0000-0000-000000000002")
    scan = {
        "status": "PARTIAL" if outcome == "partial" else "SUCCESS",
        "run_id": "scan",
    }
    ai_enabled = outcome.startswith("ai_")
    ai_result = (
        {"status": "FAILED", "reason": "enrichment_unavailable"}
        if outcome == "ai_failure"
        else {"status": "PARTIAL" if outcome == "ai_partial" else "SUCCESS"}
    )
    expected_status = (
        "PARTIAL" if outcome in ("ai_partial", "ai_failure") else scan["status"]
    )
    ai_phase = {"llm_enrichment": ai_result} if ai_enabled else {}

    def pending(**kwargs):
        assert kwargs == {
            **context,
            "enrichment_fingerprint": "fingerprint",
            "limit": 7,
        }
        return [{"signal_id": "signal"}]

    def persist(**kwargs):
        assert kwargs["signal_id"] == "signal"
        assert kwargs["enrichment_fingerprint"] == "fingerprint"
        return True

    def enrichment(*, settings, load_candidates, persist_enrichment):
        assert settings == {"configured": True}
        calls.append("ai")
        if outcome == "ai_failure":
            raise RuntimeError("private provider details")
        signal = load_candidates(contract_fingerprint="fingerprint", limit=7)[0]
        assert persist_enrichment(
            signal,
            SimpleNamespace(llm_score=75),
            {"summary": "analysis"},
            80,
            contract_fingerprint="fingerprint",
        )
        return ai_result

    for name, attributes in {
        "market_analysis.llm_enrichment_repository": {
            "MarketAnalysisLLMEnrichmentRepository": lambda session: SimpleNamespace(
                list_pending_context=pending,
                apply=persist,
            ),
        },
        "services.market_analysis_llm_enrichment": {
            "run_configured_market_analysis_llm_enrichment": enrichment,
        },
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    def context_instruments(**kwargs):
        assert kwargs == context
        calls.append("members")
        return [member] * count

    def prepare_context_scan(**kwargs):
        assert kwargs == context
        calls.append("lock")
        return "universe"

    def start():
        calls.append("start")
        return run_id

    def scan_members(session, **kwargs):
        assert session is db
        assert kwargs["members"] == [{"universe_id": "universe", **member}]
        calls.append("scan")
        if outcome == "failure":
            raise RuntimeError("provider unavailable")
        return scan

    def snapshot(**kwargs):
        assert kwargs == {"run_id": run_id}
        calls.append("snapshot")
        return snapshot_id

    def complete(**kwargs):
        assert kwargs == {
            "run_id": run_id,
            "status": expected_status,
            "discovery_snapshot_id": None,
            "scan_run_id": "scan",
            "phase_results": {
                "context": context,
                "analysis_scan": scan,
                "ranking": {"status": "SUCCESS"},
                **ai_phase,
            },
        }
        calls.append("complete")

    modules = {
        "market_analysis.scan": {"ScanConfig": object, "ScanMember": lambda **kw: kw},
        "market_screener.api_repository": {
            "MarketScreenerApiRepository": lambda session: SimpleNamespace(
                context_instruments=context_instruments,
                prepare_context_scan=prepare_context_scan,
            )
        },
        "market_screener.config": {"ScreenerRankingConfig": object},
        "market_screener.ranking": {"rank_opportunities": lambda *args, **kw: None},
        "market_screener.repository": {
            "MarketScreenerRepository": lambda session: session
        },
        "market_screener.run_repository": {
            "MarketScreenerRunRepository": lambda session: SimpleNamespace(
                start=start, create_snapshot=snapshot, complete=complete
            )
        },
        "notifications.queue": {"NotificationQueue": object},
        "services.market_analysis_scan": {"run_member_analysis_scan": scan_members},
    }
    with monkeypatch.context() as patch:
        for name, attributes in modules.items():
            module = ModuleType(name)
            module.__dict__.update(attributes)
            patch.setitem(sys.modules, name, module)
        namespace = runpy.run_path("market_screener/service.py")

    def ranking(*args, **kwargs):
        calls.append("ranking")
        return {"status": "SUCCESS"}

    namespace["analyze_screener_context"].__globals__[
        "build_market_screener_ranking"
    ] = ranking
    analyze = namespace["analyze_screener_context"]
    kwargs = {**context, "market_data_service": object(), "config": object()}
    if ai_enabled:
        kwargs.update(llm_enabled=True, llm_settings={"configured": True})
    if outcome in ("empty", "too_many"):
        with pytest.raises(ValueError):
            analyze(db, **kwargs)
        assert calls == ["members"]
    elif outcome == "failure":
        with pytest.raises(RuntimeError, match="provider unavailable"):
            analyze(db, **kwargs)
        assert calls == ["members", "lock", "start", "scan"]
    else:
        result = analyze(db, **kwargs)
        assert result == {
            "status": expected_status,
            "run_id": str(run_id),
            "ranking_snapshot_id": str(snapshot_id),
            "scan": scan,
            "ranking": {"status": "SUCCESS"},
            **ai_phase,
        }
        assert calls == [
            "members",
            "lock",
            "start",
            "scan",
            *(["ai"] if ai_enabled else []),
            "ranking",
            "snapshot",
            "complete",
        ]


@pytest.mark.parametrize(
    "scenario, expected_status",
    [
        ("analyze_success", 200),
        ("analyze_partial", 200),
        ("analyze_missing", 404),
        ("analyze_busy", 409),
        ("analyze_empty", 422),
        ("analyze_failure", 500),
        ("analyze_commit_failure", 500),
        ("analyze_unauthenticated", 401),
        ("analyze_invalid_type", 422),
        ("analyze_invalid_id", 422),
        ("contexts_success", 200),
        ("contexts_unauthenticated", 401),
        ("filter_success", 200),
        ("filter_missing", 404),
        ("filter_without_type", 422),
        ("filter_without_id", 422),
    ],
)
def test_screener_context_http_contract(monkeypatch, scenario, expected_status):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import DBAPIError

    calls = []
    context_id = "00000000-0000-0000-0000-000000000001"
    context = {
        "user_id": "owner",
        "context_type": "WATCHLIST",
        "context_id": context_id,
    }
    settings = object()
    provider = object()
    config = object()
    result = {"status": "PARTIAL" if scenario == "analyze_partial" else "SUCCESS"}

    def current_user():
        if scenario.endswith("unauthenticated"):
            raise fastapi.HTTPException(401, "Authentication required")
        return {"user_id": "owner"}

    def database_error(code):
        original = RuntimeError("database rejected context")
        original.pgcode = code
        return DBAPIError(None, None, original)

    def commit():
        calls.append("commit")
        if scenario == "analyze_commit_failure":
            raise RuntimeError("commit failed")

    db = SimpleNamespace(
        commit=commit,
        rollback=lambda: calls.append("rollback"),
        close=lambda: calls.append("close"),
    )

    def session_factory():
        calls.append("session")
        return db

    def analyze(session, **kwargs):
        assert session is db
        assert kwargs == {**context, "market_data_service": provider, "config": config}
        calls.append("analyze")
        if scenario == "analyze_missing":
            raise database_error("P0002")
        if scenario == "analyze_busy":
            raise database_error("55P03")
        if scenario == "analyze_empty":
            raise ValueError("empty context")
        if scenario == "analyze_failure":
            raise RuntimeError("provider unavailable")
        return result

    def list_contexts(**kwargs):
        assert kwargs == {"user_id": "owner"}
        calls.append("contexts")
        return [
            {
                "context_type": "WATCHLIST",
                "context_id": context_id,
                "name": "My list",
                "instrument_count": 1,
            }
        ]

    def members(**kwargs):
        assert kwargs == context
        calls.append("members")
        if scenario == "filter_missing":
            raise database_error("P0002")
        return []

    def opportunities(**kwargs):
        assert {key: kwargs[key] for key in context} == context
        assert kwargs["strategy_code"] == "BREAKOUT"
        assert kwargs["consensus_direction"] == "LONG"
        assert kwargs["limit"] == 20
        calls.append("opportunities")
        return []

    modules = {
        "auth.security": {"CurrentUser": dict, "get_current_user": current_user},
        "db": {"SessionLocal": session_factory},
        "market_screener.api_repository": {
            "MarketScreenerApiRepository": lambda session: SimpleNamespace(
                list_contexts=list_contexts,
                context_instruments=members,
                list_opportunities=opportunities,
            )
        },
        "market_analysis.lifecycle_repository": {"SignalLifecycleRepository": object},
        "config.settings": {"settings": settings},
        "market_analysis.scan": {
            "ScanConfig": SimpleNamespace(from_settings=lambda value: config)
        },
        "market_data.factory": {"market_data": provider},
        "market_screener.service": {"analyze_screener_context": analyze},
    }
    with monkeypatch.context() as patch:
        for name, attributes in modules.items():
            module = ModuleType(name)
            module.__dict__.update(attributes)
            patch.setitem(sys.modules, name, module)
        router = runpy.run_path("routers/market_screener.py")["router"]
        app = fastapi.FastAPI()
        app.include_router(router, prefix="/api/screener")
        with TestClient(app, raise_server_exceptions=False) as client:
            if scenario.startswith("analyze"):
                body = {"context_type": "WATCHLIST", "context_id": context_id}
                if scenario == "analyze_invalid_type":
                    body["context_type"] = "ALL"
                if scenario == "analyze_invalid_id":
                    body["context_id"] = "invalid"
                response = client.post("/api/screener/analyze", json=body)
            elif scenario.startswith("contexts"):
                response = client.get("/api/screener/contexts")
            else:
                params = {
                    "context_type": "WATCHLIST",
                    "context_id": context_id,
                    "strategy_code": " breakout ",
                    "consensus_direction": "LONG",
                    "limit": 20,
                }
                if scenario == "filter_without_type":
                    del params["context_type"]
                if scenario == "filter_without_id":
                    del params["context_id"]
                response = client.get("/api/screener", params=params)
    assert response.status_code == expected_status
    if scenario in ("analyze_success", "analyze_partial"):
        assert response.json() == result
        assert calls == ["session", "analyze", "commit", "close"]
    elif scenario == "filter_success":
        assert response.json() == []
        assert calls == ["session", "members", "opportunities", "close"]
    elif scenario == "contexts_success":
        assert response.json()[0]["context_id"] == context_id
        assert calls == ["session", "contexts", "close"]
    elif scenario.endswith(
        ("unauthenticated", "invalid_type", "invalid_id", "without_type", "without_id")
    ):
        assert calls == []
    elif scenario.startswith("analyze"):
        assert calls[-2:] == ["rollback", "close"]
        assert ("commit" in calls) == (scenario == "analyze_commit_failure")
    else:
        assert calls == ["session", "members", "close"]


def test_gui_context_analysis_and_optional_refresh_contract() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    service = Path("../mip-frontend/src/services/marketScreener.ts").read_text(
        encoding="utf-8"
    )
    assert 'api.get<ScreenerContext[]>("/screener/contexts")' in service
    assert 'api.post<ScreenerAnalysisResult>("/screener/analyze", context)' in service
    assert "mutationFn: analyzeScreenerContext" in page
    assert "await refreshScreener()" in page
    assert "context_type: selectedContext?.context_type" in page
    assert "context_id: selectedContext?.context_id" in page
    assert "strategy_code: strategy.trim().toUpperCase() || undefined" in page
    assert "useState(false)" in page.split("const [autoRefresh", 1)[1].split(";", 1)[0]
    assert "autoRefresh && !analysis.isPending ? 60_000 : false" in page
    assert "analysis.error.response?.status === 409" in page
    assert "contextsQuery.refetch()" in page


def test_gui_handles_missing_signal_arrays() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "selected.signal_reasons ?? []" in page


def test_gui_snapshot_changes_open_only_current_opportunity() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    columns = page.split("const changeColumns:", 1)[1].split("const rows =", 1)[0]
    assert "disabled={!row.current}" in columns
    assert "if (!row.current) return;" in columns
    assert "setSelectedId(row.current.opportunity_id)" in columns
    assert "row.previous.opportunity_id" not in columns
    assert columns.index("setChangesOpen(false)") < columns.index("setSelectedId(")
    assert "disableExport: true" in columns
    assert "<OpenInNewIcon" in columns


def test_gui_screener_refreshes_only_selected_details() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    refresh = page.split("function refreshScreener()", 1)[1].split("return (", 1)[0]
    for query in ("opportunitiesQuery", "changesQuery", "runsQuery", "snapshotsQuery"):
        assert f"{query}.refetch()" in refresh
    assert "selectedId ? [detailQuery.refetch()] : []" in refresh
    assert "selectedId && selected ? [signalEventsQuery.refetch()] : []" in refresh
    assert "selectedSnapshotId ? [snapshotItemsQuery.refetch()] : []" in refresh
    assert "disabled={refreshing || analysis.isPending}" in page
    assert "onClick={() => void refreshScreener()}" in page


def test_gui_snapshot_history_distinguishes_failures_from_empty_results() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    history = page.split("<DialogTitle>Storico Screener</DialogTitle>", 1)[1]
    assert "snapshotsQuery.isLoading" in history
    assert "snapshotsQuery.isError" in history
    assert "snapshotsQuery.isSuccess && !snapshotsQuery.data.length" in history
    assert "selectedSnapshotId && snapshotItemsQuery.isError" in history
    assert "snapshotsQuery.refetch()" in history
    assert "snapshotItemsQuery.refetch()" in history
    assert "loading={snapshotItemsQuery.isFetching}" in history


def test_gui_historical_comparison_keeps_latest_entry_indicators_separate() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    assert 'queryKey: ["market-screener-changes", changesSnapshotId]' in page
    assert "getScreenerSnapshotChanges(changesSnapshotId)" in page
    assert "enabled: changesOpen && Boolean(changesSnapshotId)" in page
    assert "changesSnapshotId ? historicalChangesQuery : changesQuery" in page
    assert "const newEntries = new Set(changesQuery.data?.changes" in page
    assert "setChangesSnapshotId(selectedSnapshotId)" in page
    assert "setHistoryOpen(false)" in page
    assert "!changeType || change.change_type === changeType" in page
    assert "rows={comparisonRows}" in page
    assert 'fileName: "mip-screener-variazioni"' in page


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
