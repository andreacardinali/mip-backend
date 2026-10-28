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
    migration = (ROOT / "scripts/fix_consolidate_instrument_aliases.sql").read_text(
        encoding="utf-8"
    )
    assert conflict_clause in migration
    assert "pg_get_functiondef(" in migration


@pytest.mark.parametrize("already_fixed", [False, True])
def test_consolidate_instrument_deletes_source_before_metadata_update(already_fixed):
    schema = (ROOT / "mip_schema.sql").read_text(encoding="utf-8")
    function = schema.split("CREATE FUNCTION public.sp_consolidate_instrument(", 1)[
        1
    ].split("CREATE FUNCTION ", 1)[0]
    migration = (ROOT / "scripts/fix_consolidate_instrument_aliases.sql").read_text(
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
    migration = (ROOT / "scripts/install_cleanup_entities.sql").read_text(
        encoding="utf-8"
    )
    functions = re.findall(r"CREATE OR REPLACE FUNCTION .*?\$\$;", migration, re.DOTALL)
    assert len(functions) == 2
    for function in functions:
        assert (
            function.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
            in schema
        )
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
    migration = (ROOT / "scripts/install_exchange_aliases.sql").read_text(
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
        (ROOT / "scripts/apply_intelligence_fixes.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "exec_driver_sql":
                assert isinstance(node.args[0], ast.Name)
