CREATE OR REPLACE FUNCTION public.sp_market_data_enqueue_stale_prices(
    p_max_age_seconds integer DEFAULT 30
)
RETURNS TABLE(candidates_count integer, enqueued_count integer)
LANGUAGE plpgsql
AS $$
DECLARE
    v_candidates_count integer := 0;
    v_enqueued_count integer := 0;
    v_instrument record;
BEGIN
    IF p_max_age_seconds < 1 THEN
        RAISE EXCEPTION 'p_max_age_seconds must be >= 1';
    END IF;

    FOR v_instrument IN
        WITH candidate_instruments AS (
            SELECT mum.instrument_id
            FROM public.market_universe_members mum
            JOIN public.market_universes mu
              ON mu.universe_id = mum.universe_id
            WHERE mu.active
              AND mum.valid_from <= now()
              AND (mum.valid_to IS NULL OR mum.valid_to > now())

            UNION

            SELECT pos.instrument_id
            FROM public.positions pos

            UNION

            SELECT i.instrument_id
            FROM public.watchlist_items wi
            JOIN public.instruments i
              ON i.entity_id = wi.entity_id
        )
        SELECT
            i.instrument_id,
            i.symbol,
            COALESCE(
                NULLIF(UPPER(BTRIM(i.data_source)), ''),
                'YAHOO'
            ) AS data_source
        FROM candidate_instruments ci
        JOIN public.instruments i
          ON i.instrument_id = ci.instrument_id
        WHERE NULLIF(BTRIM(i.symbol), '') IS NOT NULL
          AND (
              i.last_price IS NULL
              OR i.price_as_of IS NULL
              OR i.price_as_of < now() - make_interval(secs => p_max_age_seconds)
          )
        ORDER BY i.instrument_id
    LOOP
        v_candidates_count := v_candidates_count + 1;

        PERFORM public.sp_enqueue_market_price_refresh(
            v_instrument.instrument_id,
            v_instrument.symbol,
            v_instrument.data_source
        );

        v_enqueued_count := v_enqueued_count + 1;
    END LOOP;

    RETURN QUERY
    SELECT v_candidates_count, v_enqueued_count;
END;
$$;