import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.apply_intelligence_fixes import FUNCTION_NAMES, main, migration_statements


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


def test_apply_disables_driver_placeholders_without_changing_sql(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(sys, "argv", ["apply_intelligence_fixes", "--apply"])
    monkeypatch.setitem(sys.modules, "db", SimpleNamespace(engine=engine))

    main()

    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = migration_statements(schema)
    assert any("Position % not found in portfolio %" in sql for sql in statements)
    assert connection.exec_driver_sql.call_count == len(statements)
    for call, statement in zip(
        connection.exec_driver_sql.call_args_list, statements, strict=True
    ):
        assert call.args == (statement,)
        assert call.kwargs == {"execution_options": {"no_parameters": True}}


def test_no_parameters_uses_parameterless_dbapi_execute(monkeypatch):
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        execute = MagicMock(wraps=engine.dialect.do_execute)
        execute_no_params = MagicMock(wraps=engine.dialect.do_execute_no_params)
        monkeypatch.setattr(engine.dialect, "do_execute", execute)
        monkeypatch.setattr(engine.dialect, "do_execute_no_params", execute_no_params)
        statement = "SELECT 'Position % not found in portfolio %'"
        result = connection.exec_driver_sql(
            statement, execution_options={"no_parameters": True}
        ).scalar_one()
        assert result == "Position % not found in portfolio %"
        execute.assert_not_called()
        execute_no_params.assert_called_once()
        assert execute_no_params.call_args.args[1] == statement
    engine.dispose()
