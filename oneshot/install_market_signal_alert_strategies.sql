CREATE OR REPLACE FUNCTION public.sp_market_screener_list_alert_candidates(
    p_limit integer
) RETURNS SETOF jsonb
LANGUAGE sql STABLE
AS $$
    SELECT to_jsonb(candidate)
    FROM (
        SELECT signal_id, instrument_id, symbol, direction, origin,
               composite_score, consensus_score, conflict_score,
               strategy_count, verdict, summary, evidence,
               supporting_strategies AS strategy_codes
        FROM public.market_screener_opportunities
        WHERE active
        ORDER BY ranking_position, opportunity_id
        LIMIT p_limit
    ) candidate;
$$;