BEGIN;

CREATE TABLE IF NOT EXISTS public.collection_intelligence_subscriptions (
    user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    collection_type text NOT NULL CHECK (collection_type IN ('PORTFOLIO', 'WATCHLIST', 'UNIVERSE')),
    collection_id uuid NOT NULL,
    enabled_categories text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, collection_type, collection_id),
    CHECK (enabled_categories <@ ARRAY['DAILY_MOVER', 'UPCOMING_CATALYST', 'BREAKING_CATALYST', 'NEWS']::text[])
);

CREATE OR REPLACE FUNCTION public.sp_list_monitoring_collections(p_user_id uuid)
RETURNS TABLE(collection_type text, collection_id uuid, collection_name text, enabled_categories text[])
LANGUAGE sql STABLE
AS $$
    WITH collections AS (
        SELECT 'PORTFOLIO'::text AS collection_type, p.portfolio_id AS collection_id,
               p.name::text AS collection_name
        FROM public.portfolios p WHERE p.user_id = p_user_id
        UNION ALL
        SELECT 'WATCHLIST', w.watchlist_id, w.name::text
        FROM public.watchlists w WHERE w.user_id = p_user_id
        UNION ALL
        SELECT 'UNIVERSE', u.universe_id, u.name::text
        FROM public.market_universes u WHERE u.active
    )
    SELECT c.*, coalesce(s.enabled_categories, '{}'::text[])
    FROM collections c
    LEFT JOIN public.collection_intelligence_subscriptions s
      ON s.user_id = p_user_id AND s.collection_type = c.collection_type
     AND s.collection_id = c.collection_id
    ORDER BY c.collection_type, c.collection_name, c.collection_id
$$;

CREATE OR REPLACE FUNCTION public.sp_save_collection_intelligence(
    p_user_id uuid, p_collection_type text, p_collection_id uuid, p_categories text[]
)
RETURNS boolean LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.sp_list_monitoring_collections(p_user_id) c
        WHERE c.collection_type = p_collection_type AND c.collection_id = p_collection_id
    ) THEN
        RETURN false;
    END IF;
    INSERT INTO public.collection_intelligence_subscriptions
        (user_id, collection_type, collection_id, enabled_categories)
    VALUES (p_user_id, p_collection_type, p_collection_id, p_categories)
    ON CONFLICT (user_id, collection_type, collection_id) DO UPDATE
        SET enabled_categories = EXCLUDED.enabled_categories, updated_at = now();
    RETURN true;
END
$$;

CREATE OR REPLACE FUNCTION public.sp_get_collection_intelligence_candidates(p_branch text)
RETURNS SETOF jsonb LANGUAGE sql STABLE
AS $$
    WITH subscriptions AS (
        SELECT u.user_id, c.*
        FROM public.users u
        CROSS JOIN LATERAL public.sp_list_monitoring_collections(u.user_id) c
        WHERE u.is_active AND (
            p_branch = ANY(c.enabled_categories)
            OR (p_branch = 'BREAKING_CATALYST' AND 'NEWS' = ANY(c.enabled_categories))
        )
    ), members AS (
        SELECT s.*, pos.instrument_id, pos.quantity
        FROM subscriptions s
        JOIN public.positions pos ON s.collection_type = 'PORTFOLIO'
            AND pos.portfolio_id = s.collection_id AND pos.quantity <> 0
        UNION
        SELECT s.*, i.instrument_id, NULL::numeric
        FROM subscriptions s
        JOIN public.watchlist_items wi ON s.collection_type = 'WATCHLIST'
            AND wi.watchlist_id = s.collection_id
        JOIN public.instruments i ON i.entity_id = wi.entity_id
        UNION
        SELECT s.*, m.instrument_id, NULL::numeric
        FROM subscriptions s
        JOIN public.market_universe_members m ON s.collection_type = 'UNIVERSE'
            AND m.universe_id = s.collection_id
            AND m.valid_from <= now() AND (m.valid_to IS NULL OR m.valid_to > now())
    ), valued AS (
        SELECT m.*, i.entity_id, i.symbol, i.exchange, i.currency_code,
               i.instrument_type, e.name,
               CASE WHEN m.collection_type = 'PORTFOLIO' AND i.last_price > 0
                    AND (
                        upper(trim(i.currency_code)) = upper(trim(p.base_currency))
                        OR (
                            EXISTS (SELECT 1 FROM public.exchange_rates er
                                WHERE er.currency_code = upper(trim(i.currency_code))
                                  AND er.rate_to_usd > 0)
                            AND EXISTS (SELECT 1 FROM public.exchange_rates er
                                WHERE er.currency_code = upper(trim(p.base_currency))
                                  AND er.rate_to_usd > 0)
                        )
                    )
                    THEN public.fx_convert(m.quantity * i.last_price,
                        i.currency_code, p.base_currency) END AS position_value
        FROM members m
        JOIN public.instruments i ON i.instrument_id = m.instrument_id
        JOIN public.entities e ON e.entity_id = i.entity_id
        LEFT JOIN public.portfolios p ON m.collection_type = 'PORTFOLIO'
            AND p.portfolio_id = m.collection_id AND p.user_id = m.user_id
    ), totals AS (
        SELECT user_id, collection_type, collection_id,
               bool_and(position_value IS NOT NULL) AS complete,
               sum(position_value) AS total_value
        FROM valued
        GROUP BY user_id, collection_type, collection_id
    ), entities AS (
        SELECT user_id, collection_type, collection_id, entity_id,
               sum(quantity) AS quantity, sum(position_value) AS market_value,
               bool_and(position_value IS NOT NULL) AS complete
        FROM valued
        GROUP BY user_id, collection_type, collection_id, entity_id
    ), representative AS (
        SELECT DISTINCT ON (user_id, collection_type, collection_id, entity_id,
            CASE WHEN p_branch = 'DAILY_MOVER' THEN instrument_id END) *
        FROM valued
        ORDER BY user_id, collection_type, collection_id, entity_id,
                 CASE WHEN p_branch = 'DAILY_MOVER' THEN instrument_id END,
                 abs(position_value) DESC NULLS LAST, symbol, instrument_id
    )
    SELECT jsonb_build_object(
        'user_id', r.user_id, 'entity_id', r.entity_id,
        'instrument_id', r.instrument_id, 'symbol', r.symbol, 'name', r.name,
        'exchange', r.exchange, 'currency_code', r.currency_code,
        'instrument_type', r.instrument_type,
        'collection_type', r.collection_type, 'collection_id', r.collection_id,
        'collection_name', r.collection_name, 'enabled_categories', r.enabled_categories,
        'quantity', CASE WHEN p_branch = 'DAILY_MOVER' THEN r.quantity ELSE a.quantity END,
        'market_value', CASE WHEN p_branch = 'DAILY_MOVER' THEN r.position_value
                            WHEN a.complete THEN a.market_value END,
        'allocation_pct', CASE WHEN t.complete AND t.total_value > 0
            THEN round((CASE WHEN p_branch = 'DAILY_MOVER' THEN r.position_value
                            ELSE a.market_value END) / t.total_value * 100, 4) END,
        'in_portfolio', r.collection_type = 'PORTFOLIO',
        'in_watchlist', r.collection_type = 'WATCHLIST',
        'related_symbols', (
            SELECT jsonb_agg(jsonb_build_object(
                'instrument_id', v.instrument_id, 'symbol', v.symbol,
                'exchange', v.exchange, 'currency', v.currency_code
            ) ORDER BY v.symbol, v.instrument_id)
            FROM valued v WHERE v.user_id = r.user_id
              AND v.collection_type = r.collection_type AND v.collection_id = r.collection_id
              AND v.entity_id = r.entity_id
        )
    )
    FROM representative r
    JOIN entities a USING (user_id, collection_type, collection_id, entity_id)
    JOIN totals t USING (user_id, collection_type, collection_id)
    ORDER BY r.user_id, r.collection_type, r.collection_id, r.name, r.entity_id
$$;

CREATE OR REPLACE FUNCTION public.sp_get_intelligence_coverage(p_branch text)
RETURNS SETOF jsonb LANGUAGE sql STABLE
AS $$
    WITH covered_instruments AS (
        SELECT instrument_id FROM public.positions WHERE quantity <> 0
        UNION
        SELECT i.instrument_id FROM public.watchlist_items wi
        JOIN public.instruments i ON i.entity_id = wi.entity_id
        UNION
        SELECT m.instrument_id FROM public.market_universe_members m
        JOIN public.market_universes u USING (universe_id)
        WHERE u.active AND m.valid_from <= now()
            AND (m.valid_to IS NULL OR m.valid_to > now())
    ), covered AS (
        SELECT i.*, e.name
        FROM covered_instruments c
        JOIN public.instruments i USING (instrument_id)
        JOIN public.entities e USING (entity_id)
    ), representative AS (
        SELECT DISTINCT ON (entity_id,
            CASE WHEN p_branch = 'DAILY_MOVER' THEN instrument_id END) *
        FROM covered
        ORDER BY entity_id,
            CASE WHEN p_branch = 'DAILY_MOVER' THEN instrument_id END,
            symbol, instrument_id
    )
    SELECT jsonb_build_object(
        'entity_id', r.entity_id, 'instrument_id', r.instrument_id,
        'symbol', r.symbol, 'name', r.name, 'exchange', r.exchange,
        'currency_code', r.currency_code, 'instrument_type', r.instrument_type,
        'related_symbols', (
            SELECT jsonb_agg(jsonb_build_object(
                'instrument_id', c.instrument_id, 'symbol', c.symbol,
                'exchange', c.exchange, 'currency', c.currency_code
            ) ORDER BY c.symbol, c.instrument_id)
            FROM covered c WHERE c.entity_id = r.entity_id
        )
    )
    FROM representative r ORDER BY r.entity_id, r.instrument_id
$$;

CREATE TABLE IF NOT EXISTS public.collection_intelligence_events (
    branch text NOT NULL CHECK (branch IN ('DAILY_MOVER', 'UPCOMING_CATALYST', 'BREAKING_CATALYST')),
    event_key text NOT NULL,
    entity_id uuid NOT NULL REFERENCES public.entities(entity_id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES public.instruments(instrument_id) ON DELETE CASCADE,
    category text NOT NULL CHECK (category IN ('DAILY_MOVER', 'UPCOMING_CATALYST', 'BREAKING_CATALYST', 'NEWS')),
    payload jsonb NOT NULL,
    evidence jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (branch, event_key)
);

CREATE OR REPLACE FUNCTION public.sp_save_intelligence_event(
    p_branch text, p_event_key text, p_entity_id uuid, p_instrument_id uuid,
    p_category text, p_payload jsonb, p_evidence jsonb
)
RETURNS void LANGUAGE sql
AS $$
    INSERT INTO public.collection_intelligence_events
        (branch, event_key, entity_id, instrument_id, category, payload, evidence)
    VALUES (p_branch, p_event_key, p_entity_id, p_instrument_id, p_category, p_payload, p_evidence)
    ON CONFLICT (branch, event_key) DO UPDATE
        SET payload = EXCLUDED.payload, evidence = EXCLUDED.evidence, last_seen_at = now()
$$;

CREATE OR REPLACE FUNCTION public.sp_get_recent_intelligence_catalysts(
    p_entity_ids uuid[], p_days integer DEFAULT 30
)
RETURNS TABLE(
    catalyst_id text, entity_id uuid, catalyst_type text,
    catalyst_date timestamptz, status text, importance numeric,
    title text, source text, symbols jsonb
)
LANGUAGE sql STABLE
AS $$
    WITH history AS (
        SELECT c.catalyst_id::text, c.entity_id, c.catalyst_type::text,
               c.catalyst_date, c.status::text, c.importance,
               c.title, c.source, c.symbols, 1 AS priority
        FROM public.sp_get_recent_catalysts(p_entity_ids, p_days) c
        UNION ALL
        SELECT e.branch || '|' || e.event_key, e.entity_id,
               e.payload->>'eventType',
               (left(e.payload->>'eventDate', 10)::date)::timestamp AT TIME ZONE 'UTC',
               CASE WHEN e.branch = 'UPCOMING_CATALYST' THEN 'EXPECTED' ELSE 'OBSERVED' END,
               coalesce((e.payload->>'eventConfidence')::numeric, 0),
               e.payload->>'headline', e.branch,
               coalesce((
                   SELECT jsonb_agg(jsonb_build_object(
                       'instrument_id', i.instrument_id, 'symbol', i.symbol,
                       'exchange', i.exchange, 'currency', i.currency_code
                   ) ORDER BY i.symbol, i.instrument_id)
                   FROM public.instruments i WHERE i.entity_id = e.entity_id
               ), '[]'::jsonb), 0
        FROM public.collection_intelligence_events e
        WHERE e.entity_id = ANY(p_entity_ids)
          AND e.branch IN ('UPCOMING_CATALYST', 'BREAKING_CATALYST')
          AND left(e.payload->>'eventDate', 10)::date >=
              (now() - make_interval(days => GREATEST(p_days, 0)))::date
    ), deduplicated AS (
        SELECT DISTINCT ON (entity_id, catalyst_type, catalyst_date, title) *
        FROM history
        ORDER BY entity_id, catalyst_type, catalyst_date, title, priority, catalyst_id
    )
    SELECT catalyst_id, entity_id, catalyst_type, catalyst_date,
           status, importance, title, source, symbols
    FROM deduplicated ORDER BY catalyst_date DESC, catalyst_id
$$;

COMMIT;