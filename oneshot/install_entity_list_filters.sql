BEGIN;

CREATE OR REPLACE FUNCTION public.sp_get_user_entities(p_user_id uuid, p_scope character varying DEFAULT 'ALL'::character varying, p_query text DEFAULT ''::text, p_limit integer DEFAULT 500) RETURNS TABLE(entity_id uuid, name character varying, entity_type character varying, country_code character varying, symbol character varying, exchange character varying, in_portfolio boolean, in_watchlist boolean, has_active_signals boolean)
    LANGUAGE sql STABLE
    AS $$
WITH matched AS (
    SELECT e.entity_id, e.name, e.entity_type::varchar, e.country_code,
        (membership.payload ->> 'in_portfolio')::boolean AS in_portfolio,
        (membership.payload ->> 'in_watchlist')::boolean AS in_watchlist,
        EXISTS (SELECT 1 FROM public.market_screener_opportunities o
            WHERE o.entity_id = e.entity_id AND o.active) AS has_active_signals
    FROM public.entities e
    CROSS JOIN LATERAL (SELECT public.sp_get_user_entity_asset_match(
        p_user_id, e.entity_id) AS payload) membership
    WHERE e.active AND p_user_id IS NOT NULL
)
SELECT m.entity_id, m.name, m.entity_type, m.country_code,
    NULL::varchar AS symbol, NULL::varchar AS exchange,
    m.in_portfolio, m.in_watchlist, m.has_active_signals
FROM matched m
WHERE (p_scope = 'ALL'
    OR (p_scope = 'PORTFOLIO' AND m.in_portfolio)
    OR (p_scope = 'WATCHLIST' AND m.in_watchlist)
    OR (p_scope = 'SIGNALS' AND m.has_active_signals))
  AND (coalesce(btrim(p_query), '') = '' OR EXISTS (
        SELECT 1 FROM unnest(ARRAY[m.name::text, m.entity_type::text]) value
    WHERE strpos(lower(value), lower(btrim(p_query))) > 0
    ) OR EXISTS (
        SELECT 1 FROM public.instruments i
        WHERE i.entity_id = m.entity_id AND EXISTS (
                SELECT 1 FROM unnest(ARRAY[i.symbol::text, i.exchange::text]) value
                WHERE strpos(lower(value), lower(btrim(p_query))) > 0
        )
  ))
ORDER BY m.name, m.entity_id
LIMIT least(greatest(p_limit, 1), 500);
$$;

COMMIT;