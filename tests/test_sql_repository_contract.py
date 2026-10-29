import ast
import re
from pathlib import Path

import pytest

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


def test_alert_rule_management_installer_matches_schema():
    installer = (ROOT / "oneshot/install_alert_rule_management.sql").read_text(
        encoding="utf-8"
    )
    pattern = r"FUNCTION public\.sp_alert_list_user_rules\(.*?\$\$;"
    installed = re.search(pattern, installer, re.DOTALL)
    assert installed is not None
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    snapshot = re.search(pattern, schema, re.DOTALL)
    assert snapshot is not None
    installed_header, installed_body = installed.group().split("$$", 1)
    snapshot_header, snapshot_body = snapshot.group().split("$$", 1)
    assert re.sub(r"\s*([()])\s*", r"\1", " ".join(installed_header.split())) == re.sub(
        r"\s*([()])\s*", r"\1", " ".join(snapshot_header.split())
    )
    assert " ".join(installed_body.split()) == " ".join(snapshot_body.split())
    assert "WHERE r.user_id = p_user_id" in installed.group()
    assert "WHERE r.enabled" not in installed.group()


@pytest.mark.parametrize(
    "name",
    ["sp_get_user_notification_preferences", "sp_upsert_user_notification_preference"],
)
def test_monitoring_notification_preferences_installer_matches_schema(name):
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    installer = (
        ROOT / "oneshot/install_monitoring_notification_preferences.sql"
    ).read_text(encoding="utf-8")
    pattern = rf"FUNCTION public\.{name}\(.*?\$\$;"
    snapshot = re.search(pattern, schema, re.DOTALL)
    installed = re.search(pattern, installer, re.DOTALL)
    assert snapshot is not None
    assert installed is not None
    assert " ".join(snapshot.group().split()) == " ".join(installed.group().split())
    for notification_type in (
        "MARKET_SIGNAL",
        "FIXED_PRICE",
        "PERCENT_MOVE",
        "SIGNAL_CHANGE",
    ):
        assert f"'{notification_type}'" in installed.group()
    if name == "sp_get_user_notification_preferences":
        assert "ns.user_id = p_user_id" in installed.group()
        assert "ns.portfolio_id IS NULL" in installed.group()
        assert "COALESCE(ns.enabled, nt.default_enabled)" in installed.group()
        assert "COALESCE(ns.source_id, ds.source_id)" in installed.group()
        for notification_type in (
            "MARKET_SIGNAL",
            "FIXED_PRICE",
            "PERCENT_MOVE",
            "SIGNAL_CHANGE",
        ):
            assert re.search(
                rf"\('{notification_type}'::varchar,.*?::text, true\)",
                installed.group(),
                re.DOTALL,
            )
    else:
        assert "us.user_id = p_user_id" in installed.group()
        assert "v_severity_min NOT IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')" in (
            installed.group()
        )
        assert "ON CONFLICT (user_id, event_type)" in installed.group()
        assert "WHERE portfolio_id IS NULL" in installed.group()


def test_alert_event_history_installer_matches_schema_and_owner_scope():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    installer = (ROOT / "oneshot/install_alert_event_history.sql").read_text(
        encoding="utf-8"
    )
    pattern = r"FUNCTION public\.sp_alert_list_user_events\(.*?\$\$;"
    snapshot = re.search(pattern, schema, re.DOTALL)
    installed = re.search(pattern, installer, re.DOTALL)
    assert snapshot is not None
    assert installed is not None
    assert " ".join(snapshot.group().split()) == " ".join(installed.group().split())
    assert "r.user_id = p_user_id" in installed.group()
    assert "e.user_id = p_user_id AND e.rule_id = p_rule_id" in installed.group()
    assert "n.user_id = p_user_id" in installed.group()
    assert "ORDER BY e.created_at DESC, e.alert_event_id DESC" in installed.group()
    assert "'notification_status', n.status" in installed.group()
    assert "'sent_at', n.sent_at" in installed.group()
    assert "LIMIT least(greatest(coalesce(p_limit, 25), 1), 100)" in installed.group()
    assert "OFFSET greatest(coalesce(p_offset, 0), 0)" in installed.group()


def test_consolidate_instrument_alias_conflicts_match_unique_index():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    function = schema.split("CREATE FUNCTION public.sp_consolidate_instrument(", 1)[
        1
    ].split("CREATE FUNCTION ", 1)[0]
    index = re.search(
        r"CREATE UNIQUE INDEX uq_instrument_alias_normalized .*?USING btree (.+);",
        schema,
    )
    assert index is not None
    conflict_clause = f"ON CONFLICT {index.group(1)} DO NOTHING"
    assert " ".join(function.split()).count(conflict_clause) == 2
    assert "ON CONFLICT (alias_symbol)" not in function
    migration = (ROOT / "oneshot/fix_consolidate_instrument_aliases.sql").read_text(
        encoding="utf-8"
    )
    assert conflict_clause in migration
    assert "pg_get_functiondef(" in migration


def test_collection_monitoring_installer_matches_schema_and_owner_scope():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    installer = (ROOT / "oneshot/install_collection_monitoring.sql").read_text(
        encoding="utf-8"
    )
    pattern = r"FUNCTION public\.sp_alert_list_rule_candidates\(.*?\$\$;"
    snapshot = re.search(pattern, schema, re.DOTALL).group()
    installed = re.search(pattern, installer, re.DOTALL).group()
    assert " ".join(snapshot.split()) == " ".join(installed.split())
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
    assert "ALTER COLUMN signal_id DROP NOT NULL" in installer
    table = schema.split("CREATE TABLE public.alert_events (", 1)[1].split(");", 1)[0]
    assert "signal_id uuid NOT NULL" not in table


@pytest.mark.parametrize("already_fixed", [False, True])
def test_consolidate_instrument_deletes_source_before_metadata_update(already_fixed):
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    function = schema.split("CREATE FUNCTION public.sp_consolidate_instrument(", 1)[
        1
    ].split("CREATE FUNCTION ", 1)[0]
    migration = (ROOT / "oneshot/fix_consolidate_instrument_aliases.sql").read_text(
        encoding="utf-8"
    )
    metadata_pattern = migration.split("$metadata$", 2)[1]
    delete_pattern = migration.split("$source_delete$", 2)[1]
    metadata_match = re.search(metadata_pattern, function)
    delete_match = re.search(delete_pattern, function)
    assert metadata_match is not None
    assert delete_match is not None
    assert (
        function.index("INSERT INTO instrument_consolidation_audit")
        < delete_match.start()
    )
    assert delete_match.start() < metadata_match.start()
    metadata = metadata_match.group()
    source_delete = delete_match.group()
    original = function
    if not already_fixed:
        function = function.replace(metadata, "", 1).replace(
            source_delete, metadata + "\n" + source_delete, 1
        )
    if function.index(metadata) < function.index(source_delete):
        function = function.replace(metadata, "").replace(
            source_delete, source_delete + "\n\n" + metadata
        )
    assert " ".join(function.split()) == " ".join(original.split())
    assert (
        "IF metadata_statement IS NULL OR source_delete_statement IS NULL" in migration
    )


def test_cleanup_entities_installation_matches_schema():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    migration = (ROOT / "oneshot/install_cleanup_entities.sql").read_text(
        encoding="utf-8"
    )
    functions = re.findall(r"CREATE OR REPLACE FUNCTION .*?\$\$;", migration, re.DOTALL)
    assert len(functions) == 2
    for function in functions:
        name = function.split("(", 1)[0].removeprefix("CREATE OR REPLACE FUNCTION ")
        matches = re.findall(
            rf"^CREATE FUNCTION {re.escape(name)}\(.*?^\$\$;",
            schema,
            re.MULTILINE | re.DOTALL,
        )
        assert len(matches) == 1, name
        expected_header, expected_body = function.replace(
            "CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1
        ).split("$$", 1)
        actual_header, actual_body = matches[0].split("$$", 1)
        assert " ".join(expected_header.split()) == " ".join(actual_header.split())
        assert expected_body == actual_body
    cleanup = functions[1]
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


def test_exchange_alias_installation_matches_schema():
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    migration = (ROOT / "oneshot/install_exchange_aliases.sql").read_text(
        encoding="utf-8"
    )
    functions = re.findall(r"CREATE OR REPLACE FUNCTION .*?\$\$;", migration, re.DOTALL)
    assert len(functions) == 2

    def normalize_function(value):
        return " ".join(
            re.sub(r"--[^\n]*", "", value)
            .replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION")
            .split()
        )

    for function in functions:
        function_name = re.search(r"FUNCTION public.(\w+)\(", function).group(1)
        installed = "CREATE FUNCTION public." + function_name + "("
        schema_function = installed + schema.split(installed, 1)[1].split("$$;", 1)[0]
        assert normalize_function(function.removesuffix("$$;")) == normalize_function(
            schema_function
        )
    registration = functions[0]
    assert "FOR KEY SHARE" in registration
    assert "AND active" in registration
    assert registration.index("LOCK TABLE") < registration.index("IF EXISTS")
    assert registration.index("exchange <> p_exchange") < registration.index(
        "exchange = p_exchange\n          AND"
    )
    assert "upper(btrim(source_exchange_name)) = v_alias" in registration
    assert "INSERT INTO public.exchanges" not in migration
    assert "DELETE FROM" not in migration


def test_migration_executes_external_sql_without_inline_ddl():
    tree = ast.parse(
        (ROOT / "oneshot/apply_intelligence_fixes.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "exec_driver_sql":
                assert isinstance(node.args[0], ast.Name)
