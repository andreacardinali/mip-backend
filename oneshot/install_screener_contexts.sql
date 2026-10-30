BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_screener_context_instruments(
    p_user_id uuid, p_context_type varchar, p_context_id uuid
) RETURNS TABLE(instrument_id uuid, entity_id uuid, symbol varchar, exchange varchar, currency_code character(3))
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    IF p_context_type = 'WATCHLIST' THEN
        IF NOT EXISTS (SELECT 1 FROM public.watchlists w
            WHERE w.watchlist_id = p_context_id AND w.user_id = p_user_id) THEN
            RAISE EXCEPTION 'Context not found' USING ERRCODE = 'P0002';
        END IF;
    ELSIF p_context_type = 'PORTFOLIO' THEN
        IF NOT EXISTS (SELECT 1 FROM public.portfolios p
            WHERE p.portfolio_id = p_context_id AND p.user_id = p_user_id) THEN
            RAISE EXCEPTION 'Context not found' USING ERRCODE = 'P0002';
        END IF;
    ELSIF p_context_type = 'UNIVERSE' THEN
        IF NOT EXISTS (SELECT 1 FROM public.market_universes u
            WHERE u.universe_id = p_context_id AND u.active) THEN
            RAISE EXCEPTION 'Context not found' USING ERRCODE = 'P0002';
        END IF;
    ELSE
        RAISE EXCEPTION 'Invalid context type' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    SELECT i.instrument_id, i.entity_id, i.symbol, i.exchange, i.currency_code
    FROM public.instruments i
    WHERE (p_context_type = 'WATCHLIST' AND EXISTS (
        SELECT 1 FROM public.watchlist_items wi
        WHERE wi.watchlist_id = p_context_id AND wi.entity_id = i.entity_id
    )) OR (p_context_type = 'PORTFOLIO' AND EXISTS (
        SELECT 1 FROM public.positions pos
        WHERE pos.portfolio_id = p_context_id AND pos.instrument_id = i.instrument_id
          AND pos.quantity <> 0
    )) OR (p_context_type = 'UNIVERSE' AND EXISTS (
        SELECT 1 FROM public.market_universe_members mum
        WHERE mum.universe_id = p_context_id AND mum.instrument_id = i.instrument_id
          AND mum.valid_to IS NULL
    ))
    ORDER BY i.symbol, i.exchange NULLS LAST, i.instrument_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_filtered_opportunities(
    p_user_id uuid, p_origin varchar, p_direction varchar, p_verdict varchar,
    p_portfolio_match boolean, p_minimum_score numeric, p_query text,
    p_consensus_direction varchar, p_strategy_code varchar,
    p_context_type varchar, p_context_id uuid, p_limit integer
) RETURNS SETOF jsonb
LANGUAGE sql STABLE
AS $$
    WITH context AS MATERIALIZED (
        SELECT * FROM public.sp_market_screener_context_instruments(
            p_user_id, p_context_type, p_context_id
        ) WHERE p_context_type IS NOT NULL
    )
    SELECT public.sp_market_screener_enrich_consensus_payload(
        public.sp_market_screener_enrich_opportunity_payload(payload, o.opportunity_id),
        o.opportunity_id
    )
    FROM public.sp_market_screener_list_user_opportunities(
        p_user_id, p_origin, p_direction, p_verdict, p_portfolio_match,
        p_minimum_score, p_query, 2147483647
    ) payload
    JOIN public.market_screener_opportunities o
      ON o.opportunity_id = (payload->>'opportunity_id')::uuid
    JOIN public.instruments i ON i.instrument_id = o.instrument_id
    WHERE (p_consensus_direction IS NULL OR o.consensus_direction = p_consensus_direction)
      AND (p_strategy_code IS NULL OR o.strategy_code = p_strategy_code
           OR o.supporting_strategies ? p_strategy_code OR o.opposing_strategies ? p_strategy_code)
      AND (p_context_type IS NULL OR EXISTS (
          SELECT 1 FROM context c WHERE c.instrument_id = o.instrument_id
             OR (p_context_type = 'WATCHLIST' AND c.entity_id = coalesce(o.entity_id, i.entity_id))
      ))
    ORDER BY o.ranking_position NULLS LAST, o.composite_score DESC, o.opportunity_id
    LIMIT least(greatest(coalesce(p_limit, 100), 1), 1000);
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_contexts(p_user_id uuid)
RETURNS TABLE(context_type varchar, context_id uuid, name varchar, instrument_count bigint)
LANGUAGE sql STABLE
AS $$
    WITH contexts AS (
        SELECT 'WATCHLIST'::varchar AS context_type, w.watchlist_id AS context_id, w.name
        FROM public.watchlists w WHERE w.user_id = p_user_id
        UNION ALL
        SELECT 'PORTFOLIO'::varchar, p.portfolio_id, p.name
        FROM public.portfolios p WHERE p.user_id = p_user_id
        UNION ALL
        SELECT 'UNIVERSE'::varchar, u.universe_id, u.name
        FROM public.market_universes u WHERE u.active
    )
    SELECT c.context_type, c.context_id, c.name, (
        SELECT count(*) FROM public.sp_market_screener_context_instruments(
            p_user_id, c.context_type, c.context_id
        )
    ) FROM contexts c ORDER BY c.context_type, c.name, c.context_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_prepare_context_scan(
    p_user_id uuid, p_context_type varchar, p_context_id uuid
) RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_universe_id uuid;
BEGIN
    PERFORM * FROM public.sp_market_screener_context_instruments(
        p_user_id, p_context_type, p_context_id
    );
    IF NOT pg_try_advisory_xact_lock(hashtext('market_screener_manual_scan')) THEN
        RAISE EXCEPTION 'Analysis already running' USING ERRCODE = '55P03';
    END IF;
    IF p_context_type = 'UNIVERSE' THEN
        RETURN p_context_id;
    END IF;
    INSERT INTO public.market_universes(code, name, universe_type, active, config)
    VALUES ('CONTEXT_' || p_context_type || '_' || p_context_id::text,
        'Context ' || p_context_id::text, 'USER_CONTEXT', false,
        jsonb_build_object('context_type', p_context_type, 'context_id', p_context_id))
    ON CONFLICT (code) DO UPDATE SET updated_at = now()
    RETURNING universe_id INTO v_universe_id;
    RETURN v_universe_id;
END;
$$;

COMMIT;