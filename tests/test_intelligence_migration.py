from pathlib import Path

import pytest

from scripts.apply_intelligence_fixes import FUNCTION_NAMES, migration_statements


def test_migration_includes_each_reviewed_function_once():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = migration_statements(schema)
    assert len(statements) == len(FUNCTION_NAMES) + 1
    assert "ALTER TABLE public.positions" in statements[0]
    for name, statement in zip(FUNCTION_NAMES, statements[1:], strict=True):
        assert statement.startswith(f"CREATE OR REPLACE FUNCTION public.{name}(")
        assert statement.endswith("$$;")


def test_migration_rejects_missing_definition():
    with pytest.raises(ValueError, match="position_cost_basis"):
        migration_statements("")


def test_migration_rejects_duplicate_definition():
    definition = "CREATE FUNCTION public.position_cost_basis() RETURNS numeric\nAS $$\nSELECT 1;\n$$;\n"
    with pytest.raises(ValueError, match="position_cost_basis"):
        migration_statements(definition + definition)
