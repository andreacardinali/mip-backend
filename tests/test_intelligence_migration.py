import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from oneshot.apply_intelligence_fixes import (
    FUNCTION_NAMES,
    FUNCTION_OVERLOADS,
    main,
    migration_statements,
)


def test_migration_includes_each_reviewed_function_once():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = migration_statements(schema)
    expected_names = [
        name
        for name in FUNCTION_NAMES
        for _ in range(len(FUNCTION_OVERLOADS.get(name, (None,))))
    ]
    assert len(statements) == len(expected_names) + 1
    assert "ALTER TABLE public.positions" in statements[0]
    for name, statement in zip(expected_names, statements[1:], strict=True):
        assert statement.startswith(f"CREATE OR REPLACE FUNCTION public.{name}(")
        assert statement.endswith(("$$;", "$_$;"))


def test_enrichment_overloads_install_shared_selection_before_wrapper():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = [
        sql
        for sql in migration_statements(schema)
        if sql.startswith(
            "CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment("
        )
    ]
    assert len(statements) == 2
    assert "p_instrument_ids uuid[]) RETURNS" in statements[0]
    assert "{quant,llm_eligible}" in statements[0]
    assert "s.strategy_code <> 'SWING_EXIT'" in statements[0]
    assert "p_limit integer DEFAULT 25) RETURNS" in statements[1]
    assert "p_enrichment_fingerprint, p_limit, NULL::uuid[]" in statements[1]


@pytest.mark.parametrize("change", ["missing", "duplicate", "unreviewed"])
def test_migration_rejects_invalid_enrichment_overloads(change):
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    definition = next(
        sql.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
        for sql in migration_statements(schema)
        if sql.startswith(
            "CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment("
        )
    )
    if change == "missing":
        schema = schema.replace(definition, "", 1)
    elif change == "duplicate":
        schema += "\n" + definition
    else:
        schema = schema.replace(
            "p_instrument_ids uuid[]) RETURNS",
            "p_instrument_ids text[]) RETURNS",
            1,
        )
    with pytest.raises(
        ValueError, match="sp_market_analysis_list_pending_llm_enrichment"
    ):
        migration_statements(schema)


def test_migration_rejects_missing_definition():
    with pytest.raises(ValueError, match="position_cost_basis"):
        migration_statements("")


def test_swing_exit_and_quant_selection_are_in_migration():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = migration_statements(schema)
    lifecycle = next(
        sql
        for sql in statements
        if "CREATE OR REPLACE FUNCTION public.sp_market_analysis_reconcile_signal_lifecycle("
        in sql
    )
    assert "signal.direction = 'LONG'" in lifecycle
    assert "signal.observed_at <= v_signal.observed_at" in lifecycle
    assert "SET status = 'CLOSED'" in lifecycle
    enrichment = next(
        sql
        for sql in statements
        if "CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment("
        in sql
    )
    assert "{quant,llm_eligible}" in enrichment
    assert "s.strategy_code <> 'SWING_EXIT'" in enrichment
    upsert = next(
        sql
        for sql in statements
        if "CREATE OR REPLACE FUNCTION public.sp_market_analysis_upsert_signal(" in sql
    )
    assert "IS DISTINCT FROM" in upsert
    assert "IF v_signal_id IS NULL THEN" in upsert


def test_scoped_expiry_migration_preserves_unobserved_instruments_and_current_signals():
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statement = next(
        sql
        for sql in migration_statements(schema)
        if sql.startswith(
            "CREATE OR REPLACE FUNCTION public.sp_market_analysis_expire_evaluated_signals("
        )
    )
    assert "jsonb_to_recordset(p_scopes)" in statement
    assert "USING (instrument_id, strategy_code, timeframe)" in statement
    assert "COALESCE(p_signal_ids, ARRAY[]::uuid[])" in statement
    assert "s.observed_at < evaluation.observed_at" in statement
    assert "FOR UPDATE OF s" in statement


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
