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
             OR (p_context_type <> 'UNIVERSE' AND c.entity_id = coalesce(o.entity_id, i.entity_id))
      ))
    ORDER BY o.ranking_position NULLS LAST, o.composite_score DESC, o.opportunity_id
    LIMIT least(greatest(coalesce(p_limit, 100), 1), 1000);
$$;

COMMIT;