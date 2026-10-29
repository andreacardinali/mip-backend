BEGIN;

ALTER TABLE public.alert_rules DROP CONSTRAINT alert_rules_alert_type_check;
ALTER TABLE public.alert_rules ADD CONSTRAINT alert_rules_alert_type_check
    CHECK (alert_type IN ('MARKET_SIGNAL', 'FIXED_PRICE', 'PERCENT_MOVE', 'SIGNAL_CHANGE'));
ALTER TABLE public.alert_events ALTER COLUMN signal_id DROP NOT NULL;

CREATE OR REPLACE FUNCTION public.sp_alert_list_rule_candidates(p_rule_id uuid, p_limit integer)
RETURNS SETOF jsonb
LANGUAGE sql STABLE
AS $$
    WITH rule AS (
        SELECT r.*, coalesce(r.conditions->>'scope', 'SCREENER') AS scope,
               nullif(r.conditions->>'scope_id', '')::uuid AS scope_id,
               make_interval(mins => coalesce((r.conditions->>'max_age_minutes')::integer, 60)) AS max_age
        FROM public.alert_rules r
        WHERE r.rule_id = p_rule_id AND r.enabled
    ), scoped AS (
        SELECT i.*, r.alert_type, r.max_age
        FROM rule r
        CROSS JOIN public.instruments i
        WHERE (
            (r.scope IN ('SCREENER', 'ALL') AND EXISTS (
                SELECT 1 FROM public.market_screener_opportunities o
                WHERE o.instrument_id = i.instrument_id
                  AND (o.active OR r.alert_type = 'SIGNAL_CHANGE')
            ))
            OR (r.scope IN ('WATCHLIST', 'ALL') AND EXISTS (
                SELECT 1 FROM public.watchlists w
                JOIN public.watchlist_items wi ON wi.watchlist_id = w.watchlist_id
                WHERE w.user_id = r.user_id AND wi.entity_id = i.entity_id
                  AND (r.scope_id IS NULL OR w.watchlist_id = r.scope_id)
            ))
            OR (r.scope IN ('PORTFOLIO', 'ALL') AND EXISTS (
                SELECT 1 FROM public.portfolios p
                JOIN public.positions pos ON pos.portfolio_id = p.portfolio_id
                WHERE p.user_id = r.user_id AND pos.instrument_id = i.instrument_id
                  AND pos.quantity <> 0
                  AND (r.scope_id IS NULL OR p.portfolio_id = r.scope_id)
            ))
        )
        AND (coalesce(jsonb_array_length(r.conditions->'symbols'), 0) = 0
             OR r.conditions->'symbols' ? upper(i.symbol))
        AND NOT coalesce(r.conditions->'excluded_symbols' ? upper(i.symbol), false)
    ), candidates AS (
        SELECT to_jsonb(o) || jsonb_build_object(
                   'strategy_codes', o.supporting_strategies,
                   'observed_at', o.updated_at
               ) AS payload, o.updated_at AS observed_at,
               o.ranking_position AS rank, o.opportunity_id AS candidate_id
        FROM scoped i
        JOIN public.market_screener_opportunities o USING (instrument_id)
        WHERE i.alert_type = 'MARKET_SIGNAL' AND o.active
          AND o.updated_at BETWEEN now() - i.max_age AND now()
        UNION ALL
        SELECT jsonb_build_object(
                   'signal_id', NULL, 'instrument_id', i.instrument_id,
                   'symbol', i.symbol, 'last_price', i.last_price,
                   'observed_at', i.price_as_of,
                   'reference_price', previous.close, 'reference_at', previous.ts
               ), i.price_as_of, 0, i.instrument_id
        FROM scoped i
        LEFT JOIN LATERAL (
            SELECT ph.close, ph.ts
            FROM public.price_history ph
            WHERE ph.instrument_id = i.instrument_id AND ph.close > 0
              AND ph.ts < date_trunc('day', i.price_as_of, 'UTC')
              AND ph.ts >= i.price_as_of - interval '7 days'
            ORDER BY ph.ts DESC, ph.price_id DESC
            LIMIT 1
        ) previous ON i.alert_type = 'PERCENT_MOVE'
        WHERE i.alert_type IN ('FIXED_PRICE', 'PERCENT_MOVE') AND i.last_price > 0
          AND i.price_as_of BETWEEN now() - i.max_age AND now()
          AND (i.alert_type <> 'PERCENT_MOVE' OR previous.close IS NOT NULL)
        UNION ALL
        SELECT jsonb_build_object(
                   'signal_id', e.signal_id, 'instrument_id', e.instrument_id,
                   'symbol', i.symbol, 'signal_event_id', e.signal_event_id,
                   'event_type', e.event_type, 'direction', e.new_direction,
                   'verdict', e.new_status, 'summary', e.reason,
                   'strategy_codes', jsonb_build_array(e.strategy_code),
                   'observed_at', e.created_at, 'evidence', e.payload
               ), e.created_at, 0, e.signal_event_id
        FROM scoped i
        JOIN public.market_analysis_signal_events e USING (instrument_id)
        WHERE i.alert_type = 'SIGNAL_CHANGE' AND e.event_type <> 'OBSERVED'
          AND e.created_at BETWEEN now() - i.max_age AND now()
    )
    SELECT payload FROM candidates
    ORDER BY rank, observed_at DESC, candidate_id
    LIMIT greatest(coalesce(p_limit, 500), 0)
$$;

COMMIT;