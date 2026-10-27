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


def test_migration_executes_external_sql_without_inline_ddl():
    tree = ast.parse(
        (ROOT / "scripts/apply_intelligence_fixes.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "exec_driver_sql":
                assert isinstance(node.args[0], ast.Name)
