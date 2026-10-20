BEGIN;

DROP FUNCTION IF EXISTS public.sp_get_watchlist_items(uuid, uuid);

CREATE FUNCTION public.sp_get_watchlist_items(
    p_watchlist_id uuid,
    p_user_id uuid
)
RETURNS TABLE(
    watchlist_item_id uuid,
    watchlist_id uuid,
    entity_id uuid,
    priority smallint,
    target_price numeric,
    notes text,
    name character varying,
    instrument_id uuid,
    symbol character varying,
    exchange character varying,
    currency_code character,
    last_price numeric
)
LANGUAGE sql
STABLE
AS $$
SELECT
    wi.watchlist_item_id,
    wi.watchlist_id,
    wi.entity_id,
    wi.priority,
    wi.target_price,
    wi.notes,
    e.name,
    i.instrument_id,
    i.symbol,
    i.exchange,
    i.currency_code,
    i.last_price
FROM watchlist_items wi
JOIN entities e
    ON e.entity_id = wi.entity_id
LEFT JOIN instruments i
    ON i.entity_id = e.entity_id
WHERE wi.watchlist_id = p_watchlist_id
  AND EXISTS (
      SELECT 1
      FROM watchlists w
      WHERE w.watchlist_id = p_watchlist_id
        AND w.user_id = p_user_id
  )
ORDER BY
    i.symbol NULLS LAST,
    e.name;
$$;

COMMIT;
