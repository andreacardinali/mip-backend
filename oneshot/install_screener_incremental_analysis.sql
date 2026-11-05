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

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[], p_min_interval_minutes integer, p_max_age_minutes integer) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE plpgsql STABLE
    AS $_$
BEGIN
    IF p_min_interval_minutes IS NULL OR p_min_interval_minutes < 0 THEN
        RAISE EXCEPTION 'p_min_interval_minutes must be nonnegative';
    END IF;
    IF p_max_age_minutes IS NULL OR p_max_age_minutes < 0 THEN
        RAISE EXCEPTION 'p_max_age_minutes must be nonnegative';
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
          OR (p_max_age_minutes > 0 AND (
              s.last_llm_analysis_at IS NULL
              OR s.last_llm_analysis_at <= statement_timestamp()
                  - make_interval(mins => p_max_age_minutes)
          ))
      )
    ORDER BY
        COALESCE(s.composite_score, s.technical_score, 0) DESC,
        s.observed_at DESC,
        s.signal_id
    LIMIT p_limit;
END;
$_$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[], p_min_interval_minutes integer) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_llm_enrichment(
        p_enrichment_fingerprint, p_limit, p_instrument_ids,
        p_min_interval_minutes, 0
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[]) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_llm_enrichment(
        p_enrichment_fingerprint, p_limit, p_instrument_ids, 30, 0
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_context_llm_enrichment(p_enrichment_fingerprint character varying, p_user_id uuid, p_context_type character varying, p_context_id uuid, p_limit integer, p_min_interval_minutes integer, p_max_age_minutes integer) RETURNS SETOF jsonb
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
        p_min_interval_minutes, p_max_age_minutes
    ) pending;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_context_llm_enrichment(p_enrichment_fingerprint character varying, p_user_id uuid, p_context_type character varying, p_context_id uuid, p_limit integer, p_min_interval_minutes integer) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_context_llm_enrichment(
        p_enrichment_fingerprint, p_user_id, p_context_type, p_context_id,
        p_limit, p_min_interval_minutes, 0
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_context_llm_enrichment(p_enrichment_fingerprint character varying, p_user_id uuid, p_context_type character varying, p_context_id uuid, p_limit integer DEFAULT 25) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_context_llm_enrichment(
        p_enrichment_fingerprint, p_user_id, p_context_type, p_context_id,
        p_limit, 30, 0
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_apply_llm_enrichment(p_signal_id uuid, p_enrichment_fingerprint character varying, p_llm_score numeric, p_llm_analysis jsonb, p_composite_score numeric DEFAULT NULL::numeric) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_signal_id IS NULL THEN
        RAISE EXCEPTION 'p_signal_id is required';
    END IF;

    IF NULLIF(BTRIM(p_enrichment_fingerprint), '') IS NULL THEN
        RAISE EXCEPTION 'p_enrichment_fingerprint cannot be empty';
    END IF;

    IF BTRIM(p_enrichment_fingerprint) !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION
            'p_enrichment_fingerprint must be a lowercase SHA-256 hexadecimal value';
    END IF;

    IF p_llm_score IS NULL OR p_llm_score < 0 OR p_llm_score > 100 THEN
        RAISE EXCEPTION 'p_llm_score must be between 0 and 100';
    END IF;

    IF p_llm_analysis IS NULL OR jsonb_typeof(p_llm_analysis) <> 'object' THEN
        RAISE EXCEPTION 'p_llm_analysis must be a JSON object';
    END IF;

    IF p_composite_score IS NOT NULL
       AND (p_composite_score < 0 OR p_composite_score > 100) THEN
        RAISE EXCEPTION 'p_composite_score must be between 0 and 100';
    END IF;

    UPDATE public.trading_signals
    SET llm_score = p_llm_score,
        composite_score = COALESCE(p_composite_score, composite_score),
        llm_analysis = p_llm_analysis,
        enrichment_fingerprint = BTRIM(p_enrichment_fingerprint),
        last_llm_analysis_at = clock_timestamp()
    WHERE signal_id = p_signal_id
      AND status IN ('CANDIDATE', 'CONFIRMED');

    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_upsert_signal(p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_signal_id uuid;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'p_payload must be a JSON object';
    END IF;

    IF UPPER(BTRIM(p_payload->>'strategy_code')) = 'SWING_EXIT' THEN
        SELECT signal_id INTO v_signal_id FROM public.trading_signals
        WHERE instrument_id = (p_payload->>'instrument_id')::uuid
          AND strategy_code = 'SWING_EXIT'
          AND timeframe = UPPER(BTRIM(p_payload->>'timeframe'))
          AND observed_at = (p_payload->>'observed_at')::timestamptz
          AND status = 'CLOSED'
        ORDER BY signal_id LIMIT 1;
        IF FOUND THEN
            RETURN v_signal_id;
        END IF;
    END IF;

    INSERT INTO public.trading_signals
    (
        instrument_id,
        universe_id,
        strategy_code,
        timeframe,
        direction,
        signal_type,
        trend,
        status,
        confidence,
        technical_score,
        financial_score,
        catalyst_score,
        composite_score,
        reference_price,
        invalidation_price,
        target_price,
        observed_at,
        evidence
    )
    VALUES
    (
        (p_payload->>'instrument_id')::uuid,
        NULLIF(p_payload->>'universe_id', '')::uuid,
        UPPER(BTRIM(p_payload->>'strategy_code')),
        UPPER(BTRIM(p_payload->>'timeframe')),
        UPPER(BTRIM(p_payload->>'direction')),
        UPPER(BTRIM(p_payload->>'signal_type')),
        UPPER(BTRIM(p_payload->>'trend')),
        'CANDIDATE',
        (p_payload->>'confidence')::numeric,
        NULLIF(p_payload->'scores'->>'technical', '')::numeric,
        NULLIF(p_payload->'scores'->>'financial', '')::numeric,
        NULLIF(p_payload->'scores'->>'catalyst', '')::numeric,
        NULLIF(p_payload->'scores'->>'composite', '')::numeric,
        (p_payload->>'reference_price')::numeric,
        NULLIF(p_payload->>'invalidation_price', '')::numeric,
        NULLIF(p_payload->>'target_price', '')::numeric,
        (p_payload->>'observed_at')::timestamptz,
        COALESCE(p_payload->'evidence', '{}'::jsonb)
            || jsonb_build_object(
                'reasons',
                COALESCE(p_payload->'reasons', '[]'::jsonb)
            )
    )
    ON CONFLICT
    (
        instrument_id,
        strategy_code,
        timeframe,
        direction
    )
    WHERE status IN ('CANDIDATE', 'CONFIRMED')
    DO UPDATE SET
        universe_id = EXCLUDED.universe_id,
        signal_type = EXCLUDED.signal_type,
        trend = EXCLUDED.trend,
        confidence = EXCLUDED.confidence,
        technical_score = EXCLUDED.technical_score,
        financial_score = EXCLUDED.financial_score,
        catalyst_score = EXCLUDED.catalyst_score,
        llm_score = CASE
            WHEN (trading_signals.evidence, trading_signals.reference_price,
                  trading_signals.technical_score, trading_signals.financial_score,
                  trading_signals.catalyst_score, trading_signals.invalidation_price,
                  trading_signals.target_price, trading_signals.confidence,
                  trading_signals.signal_type, trading_signals.trend,
                  trading_signals.universe_id)
                 IS DISTINCT FROM
                 (EXCLUDED.evidence, EXCLUDED.reference_price,
                  EXCLUDED.technical_score, EXCLUDED.financial_score,
                  EXCLUDED.catalyst_score, EXCLUDED.invalidation_price,
                  EXCLUDED.target_price, EXCLUDED.confidence,
                  EXCLUDED.signal_type, EXCLUDED.trend,
                  EXCLUDED.universe_id)
            THEN NULL ELSE trading_signals.llm_score
        END,
        composite_score = EXCLUDED.composite_score,
        reference_price = EXCLUDED.reference_price,
        invalidation_price = EXCLUDED.invalidation_price,
        target_price = EXCLUDED.target_price,
        observed_at = EXCLUDED.observed_at,
        evidence = EXCLUDED.evidence,
        llm_analysis = CASE
            WHEN (trading_signals.evidence, trading_signals.reference_price,
                  trading_signals.technical_score, trading_signals.financial_score,
                  trading_signals.catalyst_score, trading_signals.invalidation_price,
                  trading_signals.target_price, trading_signals.confidence,
                  trading_signals.signal_type, trading_signals.trend,
                  trading_signals.universe_id)
                 IS DISTINCT FROM
                 (EXCLUDED.evidence, EXCLUDED.reference_price,
                  EXCLUDED.technical_score, EXCLUDED.financial_score,
                  EXCLUDED.catalyst_score, EXCLUDED.invalidation_price,
                  EXCLUDED.target_price, EXCLUDED.confidence,
                  EXCLUDED.signal_type, EXCLUDED.trend,
                  EXCLUDED.universe_id)
            THEN NULL ELSE trading_signals.llm_analysis
        END,
        enrichment_fingerprint = CASE
            WHEN (trading_signals.evidence, trading_signals.reference_price,
                  trading_signals.technical_score, trading_signals.financial_score,
                  trading_signals.catalyst_score, trading_signals.invalidation_price,
                  trading_signals.target_price, trading_signals.confidence,
                  trading_signals.signal_type, trading_signals.trend,
                  trading_signals.universe_id)
                 IS DISTINCT FROM
                 (EXCLUDED.evidence, EXCLUDED.reference_price,
                  EXCLUDED.technical_score, EXCLUDED.financial_score,
                  EXCLUDED.catalyst_score, EXCLUDED.invalidation_price,
                  EXCLUDED.target_price, EXCLUDED.confidence,
                  EXCLUDED.signal_type, EXCLUDED.trend,
                  EXCLUDED.universe_id)
            THEN NULL ELSE trading_signals.enrichment_fingerprint
        END
    WHERE (trading_signals.observed_at, trading_signals.evidence,
           trading_signals.reference_price, trading_signals.technical_score,
           trading_signals.financial_score, trading_signals.catalyst_score,
           trading_signals.invalidation_price, trading_signals.target_price,
           trading_signals.confidence, trading_signals.signal_type,
           trading_signals.trend, trading_signals.universe_id)
          IS DISTINCT FROM
          (EXCLUDED.observed_at, EXCLUDED.evidence, EXCLUDED.reference_price,
           EXCLUDED.technical_score, EXCLUDED.financial_score,
           EXCLUDED.catalyst_score, EXCLUDED.invalidation_price,
           EXCLUDED.target_price, EXCLUDED.confidence, EXCLUDED.signal_type,
           EXCLUDED.trend, EXCLUDED.universe_id)
    RETURNING signal_id INTO v_signal_id;

    IF v_signal_id IS NULL THEN
        SELECT signal_id INTO v_signal_id FROM public.trading_signals
        WHERE instrument_id = (p_payload->>'instrument_id')::uuid
          AND strategy_code = UPPER(BTRIM(p_payload->>'strategy_code'))
          AND timeframe = UPPER(BTRIM(p_payload->>'timeframe'))
          AND direction = UPPER(BTRIM(p_payload->>'direction'))
          AND status IN ('CANDIDATE', 'CONFIRMED');
    END IF;
    RETURN v_signal_id;
END;
$$;

COMMIT;