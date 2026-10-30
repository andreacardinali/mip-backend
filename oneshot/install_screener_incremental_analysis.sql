BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_screener_session_analysis_lock(p_release boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT CASE WHEN p_release
        THEN pg_advisory_unlock(hashtext('market_screener_analysis'))
        ELSE pg_try_advisory_lock(hashtext('market_screener_analysis')) END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_update_progress(p_run_id uuid, p_phases jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.market_screener_runs
    SET phase_results = phase_results || p_phases
    WHERE screener_run_id = p_run_id AND status = 'RUNNING';
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_context_progress(p_user_id uuid, p_context_type character varying, p_context_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_run jsonb;
BEGIN
    PERFORM 1 FROM public.sp_market_screener_context_instruments(
        p_user_id, p_context_type, p_context_id
    );
    SELECT to_jsonb(r) INTO v_run FROM public.market_screener_runs r
    WHERE r.phase_results #>> '{context,user_id}' = p_user_id::text
      AND r.phase_results #>> '{context,context_type}' = p_context_type
      AND r.phase_results #>> '{context,context_id}' = p_context_id::text
    ORDER BY r.started_at DESC, r.screener_run_id DESC LIMIT 1;
    RETURN v_run;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[], p_min_interval_minutes integer) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE plpgsql STABLE
    AS $_$
BEGIN
    IF p_min_interval_minutes IS NULL OR p_min_interval_minutes < 0 THEN
        RAISE EXCEPTION 'p_min_interval_minutes must be nonnegative';
    END IF;
    IF p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 500';
    END IF;

    IF NULLIF(BTRIM(p_enrichment_fingerprint), '') IS NULL THEN
        RAISE EXCEPTION 'p_enrichment_fingerprint cannot be empty';
    END IF;

    IF BTRIM(p_enrichment_fingerprint) !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION
            'p_enrichment_fingerprint must be a lowercase SHA-256 hexadecimal value';
    END IF;

    RETURN QUERY
    SELECT
        s.signal_id,
        s.instrument_id,
        i.entity_id,
        i.symbol,
        i.exchange,
        e.name,
        s.strategy_code,
        s.timeframe,
        s.direction,
        s.signal_type,
        s.trend,
        s.status,
        s.confidence,
        s.technical_score,
        s.financial_score,
        s.catalyst_score,
        s.reference_price,
        s.invalidation_price,
        s.target_price,
        s.observed_at,
        s.evidence
    FROM public.trading_signals AS s
    JOIN public.instruments AS i
      ON i.instrument_id = s.instrument_id
    JOIN public.entities AS e
      ON e.entity_id = i.entity_id
    WHERE s.status IN ('CANDIDATE', 'CONFIRMED')
            AND (p_instrument_ids IS NULL OR s.instrument_id = ANY(p_instrument_ids))
            AND s.strategy_code <> 'SWING_EXIT'
            AND COALESCE(s.evidence #> '{quant,llm_eligible}', 'true'::jsonb) = 'true'::jsonb
        AND (p_min_interval_minutes = 0 OR NOT EXISTS (
            SELECT 1 FROM public.trading_signals recent
            WHERE recent.instrument_id = s.instrument_id
            AND recent.last_llm_analysis_at > statement_timestamp()
                - make_interval(mins => p_min_interval_minutes)
        ))
      AND (
          s.llm_analysis IS NULL
          OR s.enrichment_fingerprint IS DISTINCT FROM
             BTRIM(p_enrichment_fingerprint)
      )
    ORDER BY
        COALESCE(s.composite_score, s.technical_score, 0) DESC,
        s.observed_at DESC,
        s.signal_id
    LIMIT p_limit;
END;
$_$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[]) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_llm_enrichment(
        p_enrichment_fingerprint, p_limit, p_instrument_ids, 30
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_context_llm_enrichment(p_enrichment_fingerprint character varying, p_user_id uuid, p_context_type character varying, p_context_id uuid, p_limit integer, p_min_interval_minutes integer) RETURNS SETOF jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_instrument_ids uuid[];
BEGIN
    SELECT array_agg(instrument_id) INTO v_instrument_ids
    FROM public.sp_market_screener_context_instruments(
        p_user_id, p_context_type, p_context_id
    );
    RETURN QUERY SELECT to_jsonb(pending)
    FROM public.sp_market_analysis_list_pending_llm_enrichment(
        p_enrichment_fingerprint, p_limit, COALESCE(v_instrument_ids, '{}'::uuid[]),
        p_min_interval_minutes
    ) pending;
END;
$$;

COMMIT;