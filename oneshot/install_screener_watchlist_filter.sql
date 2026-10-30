BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_filtered_opportunities(p_user_id uuid, p_origin character varying, p_direction character varying, p_verdict character varying, p_portfolio_match boolean, p_minimum_score numeric, p_query text, p_consensus_direction character varying, p_strategy_code character varying, p_context_type character varying, p_context_id uuid, p_limit integer, p_watchlist_match boolean) RETURNS SETOF jsonb
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
      AND (p_watchlist_match IS NULL OR (
          public.sp_get_user_entity_asset_match(p_user_id, coalesce(o.entity_id, i.entity_id), o.instrument_id)
          ->> 'in_watchlist'
      )::boolean = p_watchlist_match)
    ORDER BY o.ranking_position NULLS LAST, o.composite_score DESC, o.opportunity_id
    LIMIT least(greatest(coalesce(p_limit, 100), 1), 1000);
$$;

COMMIT;