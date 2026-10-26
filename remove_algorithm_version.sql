BEGIN;

LOCK TABLE public.trading_signals IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.market_analysis_scan_runs IN ACCESS EXCLUSIVE MODE;

DO $block$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.trading_signals
        WHERE status IN ('CANDIDATE', 'CONFIRMED')
        GROUP BY
            instrument_id,
            strategy_code,
            timeframe,
            direction
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Cannot remove algorithm_version: duplicate active trading signals exist';
    END IF;
END;
$block$;

DROP FUNCTION IF EXISTS
public.sp_market_analysis_list_pending_llm_enrichment(
    character varying,
    character varying,
    integer
);

DROP FUNCTION IF EXISTS
public.sp_market_analysis_start_scan_run(
    uuid,
    character varying,
    jsonb
);

CREATE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(
    p_prompt_version character varying
        DEFAULT 'market-analysis-enrichment-v1'::character varying,
    p_limit integer DEFAULT 25
)
RETURNS TABLE
(
    signal_id uuid,
    instrument_id uuid,
    entity_id uuid,
    symbol character varying,
    exchange character varying,
    entity_name character varying,
    strategy_code character varying,
    timeframe character varying,
    direction character varying,
    signal_type character varying,
    trend character varying,
    status character varying,
    confidence numeric,
    technical_score numeric,
    financial_score numeric,
    catalyst_score numeric,
    reference_price numeric,
    invalidation_price numeric,
    target_price numeric,
    observed_at timestamp with time zone,
    evidence jsonb
)
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
    IF p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 500';
    END IF;

    IF NULLIF(BTRIM(p_prompt_version), '') IS NULL THEN
        RAISE EXCEPTION 'p_prompt_version cannot be empty';
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
      AND (
          s.llm_analysis IS NULL
          OR s.prompt_version IS DISTINCT FROM p_prompt_version
      )
    ORDER BY
        COALESCE(s.composite_score, s.technical_score, 0) DESC,
        s.observed_at DESC,
        s.signal_id
    LIMIT p_limit;
END;
$function$;

CREATE FUNCTION public.sp_market_analysis_start_scan_run(
    p_universe_id uuid,
    p_config jsonb DEFAULT '{}'::jsonb
)
RETURNS uuid
LANGUAGE plpgsql
AS $function$
DECLARE
    v_run_id uuid;
BEGIN
    INSERT INTO public.market_analysis_scan_runs
    (
        universe_id,
        config
    )
    VALUES
    (
        p_universe_id,
        COALESCE(p_config, '{}'::jsonb)
    )
    RETURNING run_id INTO v_run_id;

    RETURN v_run_id;
END;
$function$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_upsert_signal(
    p_payload jsonb
)
RETURNS uuid
LANGUAGE plpgsql
AS $function$
DECLARE
    v_signal_id uuid;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'p_payload must be a JSON object';
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
        llm_score,
        composite_score,
        reference_price,
        invalidation_price,
        target_price,
        observed_at,
        evidence,
        llm_analysis,
        prompt_version
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
        NULLIF(p_payload->'scores'->>'llm', '')::numeric,
        NULLIF(p_payload->'scores'->>'composite', '')::numeric,
        (p_payload->>'reference_price')::numeric,
        NULLIF(p_payload->>'invalidation_price', '')::numeric,
        NULLIF(p_payload->>'target_price', '')::numeric,
        (p_payload->>'observed_at')::timestamptz,
        COALESCE(p_payload->'evidence', '{}'::jsonb)
            || jsonb_build_object(
                'reasons',
                COALESCE(p_payload->'reasons', '[]'::jsonb)
            ),
        p_payload->'llm_analysis',
        NULLIF(BTRIM(p_payload->>'prompt_version'), '')
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
        llm_score = EXCLUDED.llm_score,
        composite_score = EXCLUDED.composite_score,
        reference_price = EXCLUDED.reference_price,
        invalidation_price = EXCLUDED.invalidation_price,
        target_price = EXCLUDED.target_price,
        observed_at = EXCLUDED.observed_at,
        evidence = EXCLUDED.evidence,
        llm_analysis = EXCLUDED.llm_analysis,
        prompt_version = EXCLUDED.prompt_version
    RETURNING signal_id INTO v_signal_id;

    RETURN v_signal_id;
END;
$function$;

DROP INDEX IF EXISTS public.uq_trading_signals_active;

CREATE UNIQUE INDEX uq_trading_signals_active
ON public.trading_signals
(
    instrument_id,
    strategy_code,
    timeframe,
    direction
)
WHERE status IN ('CANDIDATE', 'CONFIRMED');

ALTER TABLE public.trading_signals
    DROP COLUMN IF EXISTS algorithm_version;

ALTER TABLE public.market_analysis_scan_runs
    DROP COLUMN IF EXISTS algorithm_version;

COMMIT;
