import ast
import re
from pathlib import Path

import pytest

from tests.schema_contract import schema_function, schema_functions, schema_sql

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "routers/portfolios.py",
    "routers/positions.py",
    "routers/watchlist_items.py",
    "routers/imports.py",
    "routers/auth.py",
    "routers/market_data.py",
    "auth/security.py",
    "market_screener/repository.py",
    "alerts/repository.py",
    "alerts/service.py",
    "scheduler.py",
    "scheduler_repository.py",
    "jobs/entities/cleanup_entities.py",
)


def sql_literals(relative_path):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "text":
            continue
        assert node.args and isinstance(node.args[0], ast.Constant), relative_path
        yield " ".join(node.args[0].value.split())


def test_scan_members_include_collections_without_duplicate_instruments():
    routine = schema_function("sp_market_analysis_list_universe_members")
    assert "FROM public.instruments i" in routine
    assert "public.watchlist_items wi" in routine
    assert "pos.quantity <> 0" in routine
    assert "p_universe_id AS universe_id" in routine
    assert routine.count("EXISTS (") == 3


@pytest.mark.parametrize(
    "name",
    [
        "sp_alert_instrument_exchange",
        "sp_alert_session_reference",
        "sp_alert_observe_threshold",
        "sp_alert_register_match",
    ],
)
def test_monitoring_routines_preserve_utc_and_threshold_guards(name):
    if name == "sp_alert_session_reference":
        routines = [
            routine for routine in schema_functions(name) if "p_opens_at" in routine
        ]
        assert len(routines) == 1
        routine = routines[0]
    else:
        routine = schema_function(name)
    if name == "sp_alert_session_reference":
        assert "ts >= p_opens_at AND ts <= p_closes_at" in routine
        assert "AT TIME ZONE" not in routine
    if name == "sp_alert_observe_threshold":
        assert "p_observed_at<=s.observed_at" in routine
        assert "pg_advisory_xact_lock" in routine
        assert "NOT existing.matched AND excluded.matched" in routine


@pytest.mark.parametrize("relative_path", RUNTIME_FILES)
def test_runtime_sql_is_select_only_without_embedded_business_queries(relative_path):
    for sql in sql_literals(relative_path):
        assert sql.upper().startswith("SELECT "), (relative_path, sql)
        assert not re.search(
            r"\b(UPDATE|INSERT|DELETE|ALTER|CREATE|DROP|JOIN|WITH|HAVING|UNION|OVER)\b"
            r"|\bGROUP\s+BY\b|\b(COUNT|SUM|AVG|BOOL_OR)\s*\(",
            sql,
            flags=re.IGNORECASE,
        ), (relative_path, sql)
        assert len(re.findall(r"\bSELECT\b", sql, re.IGNORECASE)) == 1


def test_runtime_sp_calls_exist_in_schema():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    for relative_path in RUNTIME_FILES:
        for sql in sql_literals(relative_path):
            for name in re.findall(r"\b(sp_[a-z_]+)\s*\(", sql):
                assert f"CREATE FUNCTION public.{name}(" in schema, (
                    relative_path,
                    name,
                )


def test_scheduler_catalog_pruning_protects_active_jobs():
    routine = schema_function("sp_scheduler_prune_undefined_jobs")
    assert "A nonempty validated job catalog is required" in routine
    assert "All defined jobs must be registered before pruning" in routine
    assert "Cannot prune jobs with unfinished runs or claimed requests" in routine
    assert routine.index("sp_scheduler_try_job_lock(") < routine.index("DELETE FROM")
    assert "WHERE NOT (jobs.job_key = ANY(p_defined_job_keys))" in routine


@pytest.mark.parametrize(
    "name", ["sp_register_scheduler_job", "sp_list_scheduler_jobs"]
)
def test_scheduler_descriptions_preserve_enabled_state(name):
    routines = schema_functions(name)
    if name == "sp_register_scheduler_job":
        routines = [routine for routine in routines if "p_description text" in routine]
    assert len(routines) == 1
    routine = routines[0]
    assert "description text" in routine
    if name == "sp_register_scheduler_job":
        assert "sj.description IS DISTINCT FROM p_description" in routine
        assert "v_registration.action := 'UPDATED'" in routine
        assert "SET enabled" not in routine


def test_alert_rule_management_includes_disabled_owner_rules():
    routine = schema_function("sp_alert_list_user_rules")
    assert "WHERE r.user_id = p_user_id" in routine
    assert "WHERE r.enabled" not in routine


@pytest.mark.parametrize(
    "name",
    ["sp_get_user_notification_preferences", "sp_upsert_user_notification_preference"],
)
def test_monitoring_notification_preferences_preserve_owner_defaults(name):
    routine = schema_function(name)
    for notification_type in (
        "MARKET_SIGNAL",
        "FIXED_PRICE",
        "PERCENT_MOVE",
        "SIGNAL_CHANGE",
    ):
        assert f"'{notification_type}'" in routine
    if name == "sp_get_user_notification_preferences":
        assert "ns.user_id = p_user_id" in routine
        assert "ns.portfolio_id IS NULL" in routine
        assert "COALESCE(ns.enabled, nt.default_enabled)" in routine
        assert "COALESCE(ns.source_id, ds.source_id)" in routine
        for notification_type in (
            "MARKET_SIGNAL",
            "FIXED_PRICE",
            "PERCENT_MOVE",
            "SIGNAL_CHANGE",
        ):
            assert re.search(
                rf"\('{notification_type}'::varchar,.*?::text, true\)",
                routine,
                re.DOTALL,
            )
    else:
        assert "us.user_id = p_user_id" in routine
        assert "v_severity_min NOT IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')" in routine
        assert "ON CONFLICT (user_id, event_type)" in routine
        assert "WHERE portfolio_id IS NULL" in routine


@pytest.mark.parametrize("name", ["sp_alert_delete_rule", "sp_alert_set_rule_enabled"])
def test_alert_management_actions_preserve_owner_and_conditions(name):
    routine = schema_function(name)
    assert "WHERE rule_id = p_rule_id AND user_id = p_user_id;" in routine
    assert "RETURN FOUND;" in routine
    if name == "sp_alert_set_rule_enabled":
        assert "SET enabled = p_enabled" in routine
        assert "IF p_enabled IS NULL THEN" in routine
        assert "conditions =" not in routine
    else:
        assert "DELETE FROM public.alert_rules" in routine
        assert "REFERENCES public.alert_rules(rule_id) ON DELETE CASCADE" in schema_sql()


def test_alert_event_history_preserves_owner_scope_and_pagination():
    routine = schema_function("sp_alert_list_user_events")
    assert "r.user_id = p_user_id" in routine
    assert "e.user_id = p_user_id AND e.rule_id = p_rule_id" in routine
    assert "n.user_id = p_user_id" in routine
    assert "ORDER BY e.created_at DESC, e.alert_event_id DESC" in routine
    assert "'notification_status', n.status" in routine
    assert (
        "'notification_skip_reason', e.payload ->> 'notification_skip_reason'"
        in routine
    )
    assert "'sent_at', n.sent_at" in routine
    assert "LIMIT least(greatest(coalesce(p_limit, 25), 1), 100)" in routine
    assert "OFFSET greatest(coalesce(p_offset, 0), 0)" in routine


def test_alert_notification_skip_preserves_status_and_unnotified_guard():
    routine = schema_function("sp_alert_mark_notification_skipped")
    assert "p_status IS NULL OR p_status NOT IN" in routine
    for status in ("DISABLED", "PREFERENCE_DISABLED", "SOURCE_NOT_FOUND", "DUPLICATE"):
        assert f"'{status}'" in routine
    assert (
        "WHERE alert_event_id = p_event_id AND notification_id IS NULL"
        in routine
    )
    assert (
        "jsonb_set(payload, '{notification_skip_reason}', to_jsonb(p_status))"
        in routine
    )


def test_consolidate_instrument_alias_conflicts_match_unique_index():
    schema = schema_sql()
    function = schema_function("sp_consolidate_instrument")
    index = re.search(
        r"CREATE UNIQUE INDEX uq_instrument_alias_normalized .*?USING btree (.+);",
        schema,
    )
    assert index is not None
    conflict_clause = f"ON CONFLICT {index.group(1)} DO NOTHING"
    assert " ".join(function.split()).count(conflict_clause) == 2
    assert "ON CONFLICT (alias_symbol)" not in function


def test_collection_monitoring_preserves_owner_scope_and_signal_changes():
    schema = schema_sql()
    installed = schema_function("sp_alert_list_rule_candidates")
    assert "w.user_id = r.user_id AND wi.entity_id = i.entity_id" in installed
    assert "p.user_id = r.user_id AND pos.instrument_id = i.instrument_id" in installed
    assert "pos.quantity <> 0" in installed
    assert "w.watchlist_id = r.scope_id" in installed
    assert "p.portfolio_id = r.scope_id" in installed
    assert "o.active OR r.alert_type = 'SIGNAL_CHANGE'" in installed
    assert "e.event_type <> 'OBSERVED'" in installed
    assert "date_trunc('day', i.price_as_of, 'UTC')" in installed
    assert "ph.close > 0" in installed
    assert "r.conditions->'excluded_symbols'" in installed
    table = schema.split("CREATE TABLE public.alert_events (", 1)[1].split(");", 1)[0]
    assert "signal_id uuid NOT NULL" not in table


def test_consolidate_instrument_deletes_source_before_metadata_update():
    function = schema_function("sp_consolidate_instrument")
    source_delete = function.index("DELETE FROM instruments")
    metadata_update = function.index("UPDATE instruments\n    SET")
    assert function.index("INSERT INTO instrument_consolidation_audit") < source_delete
    assert source_delete < metadata_update
    assert "instruments.exchange,\n            v_source.exchange" in function


def test_cleanup_entities_preserves_locked_or_referenced_entities():
    cleanup = schema_function("sp_cleanup_orphan_entity")
    assert "FOR UPDATE SKIP LOCKED" in cleanup
    assert cleanup.index("FOR UPDATE SKIP LOCKED") < cleanup.index("DELETE FROM")
    assert "AND NOT EXISTS" in cleanup
    assert "WHEN foreign_key_violation THEN" in cleanup
    assert "DELETE FROM public.instruments" not in cleanup


def test_exchange_alias_normalization_precedes_existing_exchange():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    function = schema.split("CREATE FUNCTION public.sp_normalize_listing_exchange(", 1)[
        1
    ].split("CREATE FUNCTION ", 1)[0]
    assert function.index("FROM public.exchange_source_mappings") < function.index(
        "FROM public.exchanges"
    )
    assert "array_agg(DISTINCT esm.exchange)" in function
    assert "cardinality(v_mapped_exchanges) > 1" in function
    assert "RAISE EXCEPTION 'Ambiguous exchange alias" in function
    assert "RETURN v_mapped_exchanges[1]" in function
    assert "AND es.active" in function
    assert "upper(btrim(esm.source_exchange_code))" in function
    assert "upper(btrim(esm.source_exchange_name))" in function
    assert "LIMIT 1" not in function
    profile = schema.split("CREATE FUNCTION public.sp_upsert_asset_profile(", 1)[
        1
    ].split("CREATE FUNCTION ", 1)[0]
    assert profile.index("public.sp_normalize_listing_exchange(") < profile.index(
        "public.sp_upsert_exchange("
    )


def test_exchange_alias_registration_preserves_existing_exchanges():
    registration = schema_function("sp_register_exchange_alias")
    assert "FOR KEY SHARE" in registration
    assert "AND active" in registration
    assert registration.index("LOCK TABLE") < registration.index("IF EXISTS")
    assert registration.index("exchange <> p_exchange") < registration.index(
        "exchange = p_exchange\n          AND"
    )
    assert "upper(btrim(source_exchange_name)) = v_alias" in registration
    assert "INSERT INTO public.exchanges" not in registration
    assert "DELETE FROM" not in registration
