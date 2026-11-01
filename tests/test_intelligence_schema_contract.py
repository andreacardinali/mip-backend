from tests.schema_contract import schema_function, schema_functions, schema_sql


def test_enrichment_schema_contract_delegates_wrappers_to_shared_selection():
    definitions = schema_functions("sp_market_analysis_list_pending_llm_enrichment")
    assert len(definitions) == 3
    shared = next(sql for sql in definitions if "p_min_interval_minutes integer" in sql)
    assert "{quant,llm_eligible}" in shared
    assert "s.strategy_code <> 'SWING_EXIT'" in shared
    scoped = next(sql for sql in definitions if "p_instrument_ids uuid[]) RETURNS" in sql)
    assert "p_instrument_ids, 30" in scoped
    unscoped = next(sql for sql in definitions if "p_limit integer DEFAULT 25) RETURNS" in sql)
    assert "p_enrichment_fingerprint, p_limit, NULL::uuid[]" in unscoped


def test_preferences_schema_contract_includes_gui_timezone():
    definitions = schema_functions("sp_update_user_preferences")
    assert len(definitions) == 2
    assert any("p_reporting_currency character) RETURNS" in sql for sql in definitions)
    timezone = next(sql for sql in definitions if "p_display_timezone text" in sql)
    assert "display_timezone = p_display_timezone" in timezone
    users = schema_sql().split("CREATE TABLE public.users (", 1)[1].split("\n);", 1)[0]
    assert "display_timezone text DEFAULT 'UTC'::text NOT NULL" in users


def test_signal_schema_contract_closes_prior_longs_and_upserts_changes():
    lifecycle = schema_function("sp_market_analysis_reconcile_signal_lifecycle")
    assert "signal.direction = 'LONG'" in lifecycle
    assert "signal.observed_at <= v_signal.observed_at" in lifecycle
    assert "SET status = 'CLOSED'" in lifecycle
    upsert = schema_function("sp_market_analysis_upsert_signal")
    assert "IS DISTINCT FROM" in upsert
    assert "IF v_signal_id IS NULL THEN" in upsert


def test_expiry_schema_contract_preserves_unobserved_instruments_and_current_signals():
    statement = schema_function("sp_market_analysis_expire_evaluated_signals")
    assert "jsonb_to_recordset(p_scopes)" in statement
    assert "USING (instrument_id, strategy_code, timeframe)" in statement
    assert "COALESCE(p_signal_ids, ARRAY[]::uuid[])" in statement
    assert "s.observed_at < evaluation.observed_at" in statement
    assert "FOR UPDATE OF s" in statement