BEGIN;
SET LOCAL lock_timeout = '10s';

ALTER TABLE public.positions
    ADD COLUMN IF NOT EXISTS cost_basis_amount numeric(20,8),
    ADD COLUMN IF NOT EXISTS cost_basis_currency character(3);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.positions'::regclass
          AND conname = 'ck_positions_cost_basis'
    ) THEN
        ALTER TABLE public.positions ADD CONSTRAINT ck_positions_cost_basis CHECK (
            (cost_basis_amount IS NULL AND cost_basis_currency IS NULL)
            OR (cost_basis_amount IS NOT NULL AND cost_basis_amount >= 0
                AND cost_basis_currency IS NOT NULL)
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.position_cost_basis(p_quantity numeric, p_avg_price numeric, p_listing_currency character, p_cost_amount numeric, p_cost_currency character, p_target_currency character) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE
        WHEN p_cost_amount IS NOT NULL
         AND upper(btrim(p_cost_currency)) = upper(btrim(p_target_currency))
        THEN p_cost_amount
        WHEN upper(btrim(p_listing_currency)) = upper(btrim(p_target_currency))
        THEN p_quantity * p_avg_price
        ELSE NULL
    END;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_portfolio_pricing_states(p_user_id uuid, p_portfolio_id uuid DEFAULT NULL::uuid) RETURNS TABLE(portfolio_id uuid, total_position_count bigint, pending_price_count bigint, price_refresh_pending boolean)
    LANGUAGE sql STABLE
    AS $$
        SELECT pf.portfolio_id,
                     COUNT(pos.position_id),
                     COUNT(pos.position_id) FILTER (WHERE i.last_price IS NULL),
                     COALESCE(BOOL_OR(q.status IN ('PENDING', 'PROCESSING')), false)
        FROM public.portfolios pf
        LEFT JOIN public.positions pos ON pos.portfolio_id = pf.portfolio_id
        LEFT JOIN public.instruments i ON i.instrument_id = pos.instrument_id
        LEFT JOIN public.market_price_refresh_queue q ON q.instrument_id = pos.instrument_id
        WHERE pf.user_id = p_user_id
            AND (p_portfolio_id IS NULL OR pf.portfolio_id = p_portfolio_id)
        GROUP BY pf.portfolio_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_set_position_cost_basis(p_position_id uuid, p_user_id uuid, p_amount numeric, p_currency character) RETURNS uuid
    LANGUAGE sql
    AS $$
    WITH updated AS (
        UPDATE public.positions pos
        SET cost_basis_amount = p_amount, cost_basis_currency = p_currency
        WHERE pos.position_id = p_position_id
          AND EXISTS (
              SELECT 1 FROM public.portfolios pf
              WHERE pf.portfolio_id = pos.portfolio_id AND pf.user_id = p_user_id
          )
        RETURNING pos.position_id
    )
    SELECT position_id FROM updated;
$$;

CREATE OR REPLACE FUNCTION public.sp_update_user_preferences(p_user_id uuid, p_reporting_currency character) RETURNS jsonb
    LANGUAGE sql
    AS $$
    WITH updated AS (
        UPDATE public.users
        SET reporting_currency = p_reporting_currency, updated_at = now()
        WHERE user_id = p_user_id
        RETURNING user_id, username, email, is_active, reporting_currency
    )
    SELECT to_jsonb(updated) FROM updated;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_alert_candidates(p_limit integer) RETURNS SETOF jsonb
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

CREATE OR REPLACE FUNCTION public.sp_scheduler_try_job_lock(p_job_id uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT pg_try_advisory_xact_lock(hashtextextended('mip.scheduler.' || p_job_id::text, 0));
$$;

CREATE OR REPLACE FUNCTION public.sp_scheduler_list_job_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT job_id FROM public.scheduler_jobs;
$$;

CREATE OR REPLACE FUNCTION public.sp_scheduler_defer_job_run_request(p_job_run_id uuid) RETURNS void
    LANGUAGE sql
    AS $$
    UPDATE public.scheduler_manual_run_requests
    SET status = 'PENDING', claimed_at = NULL
    WHERE job_run_id = p_job_run_id AND status = 'PROCESSING';
$$;

CREATE OR REPLACE FUNCTION public.sp_scheduler_recover_job_runs(p_job_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_recovered integer;
BEGIN
    IF NOT public.sp_scheduler_try_job_lock(p_job_id) THEN
        RETURN 0;
    END IF;

    UPDATE public.scheduler_job_runs
    SET status = 'FAILED', completed_at = now(),
        error_message = 'Recovered orphaned run during scheduler startup'
    WHERE job_id = p_job_id AND status = 'RUNNING';
    GET DIAGNOSTICS v_recovered = ROW_COUNT;

    UPDATE public.scheduler_manual_run_requests request
    SET status = 'FAILED', completed_at = now(),
        error_message = 'Recovered orphaned manual run'
    WHERE job_id = p_job_id AND status = 'PROCESSING'
      AND EXISTS (
          SELECT 1 FROM public.scheduler_job_runs run
          WHERE run.job_run_id = request.job_run_id
      );

    UPDATE public.scheduler_manual_run_requests
    SET status = 'PENDING', claimed_at = NULL
    WHERE job_id = p_job_id AND status = 'PROCESSING'
      AND claimed_at < now() - interval '5 minutes';
    RETURN v_recovered;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_upsert_watchlist_item(p_watchlist_item_id uuid, p_watchlist_id uuid, p_user_id uuid, p_entity_id uuid, p_priority smallint, p_target_price numeric, p_notes text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_watchlist_item_id UUID;
BEGIN

	IF p_watchlist_id IS NOT NULL AND p_watchlist_id IN (SELECT watchlist_id FROM watchlists WHERE user_id = p_user_id) THEN
		IF p_watchlist_item_id IS NULL THEN
	
	        INSERT INTO watchlist_items
	        (
	            watchlist_id,
	            entity_id,
	            priority,
	            target_price,
	            notes
	        )
	        VALUES
	        (
	            p_watchlist_id,
	            p_entity_id,
	            p_priority,
	            p_target_price,
	            p_notes
	        )
	        RETURNING watchlist_item_id
	        INTO v_watchlist_item_id;
	
	    ELSE
	
	        UPDATE watchlist_items
	        SET
	            entity_id = p_entity_id,
	            priority = p_priority,
	            target_price =
	                p_target_price,
	            notes = p_notes
	        WHERE watchlist_item_id =
                  p_watchlist_item_id
              AND watchlist_id = p_watchlist_id
            RETURNING watchlist_item_id
            INTO v_watchlist_item_id;
	
            IF v_watchlist_item_id IS NULL THEN
                RAISE EXCEPTION 'Watchlist item not found or not accessible'
                    USING ERRCODE = '23503';
            END IF;
	
	    END IF;
	ELSE
        RAISE EXCEPTION 'Watchlist not found or not accessible'
            USING ERRCODE = '23503';
	END IF;

    RETURN v_watchlist_item_id;

END;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_intelligence_candidates() RETURNS TABLE(user_id uuid, entity_id uuid, instrument_id uuid, symbol character varying, name character varying, exchange character varying, currency_code character, instrument_type character varying, quantity numeric, market_value numeric, allocation_pct numeric, in_portfolio boolean, in_watchlist boolean)
    LANGUAGE sql STABLE
    AS $$
WITH portfolio_rows AS
(
    SELECT
        pf.user_id,
        i.entity_id,
        i.instrument_id,
        i.symbol,
        e.name,
        i.exchange,
        i.currency_code,
        i.instrument_type,
        SUM(p.quantity)::numeric AS quantity,
        SUM(
            COALESCE(
                public.fx_convert(
                    p.quantity * i.last_price,
                    i.currency_code,
                    u.reporting_currency
                ),
                0
            )
        )::numeric AS market_value
    FROM positions p
    JOIN portfolios pf
      ON pf.portfolio_id = p.portfolio_id
    JOIN instruments i
      ON i.instrument_id = p.instrument_id
    JOIN entities e
      ON e.entity_id = i.entity_id
    JOIN users u
      ON u.user_id = pf.user_id
     AND u.is_active = true
    WHERE e.active = true
      AND i.symbol IS NOT NULL
    GROUP BY
        pf.user_id,
        i.entity_id,
        i.instrument_id,
        i.symbol,
        e.name,
        i.exchange,
        i.currency_code,
        i.instrument_type
),
watchlist_rows AS
(
    SELECT DISTINCT
        w.user_id,
        i.entity_id,
        i.instrument_id,
        i.symbol,
        e.name,
        i.exchange,
        i.currency_code,
        i.instrument_type
    FROM watchlist_items wi
    JOIN watchlists w
      ON w.watchlist_id = wi.watchlist_id
    JOIN users u
      ON u.user_id = w.user_id
     AND u.is_active = true
    JOIN entities e
      ON e.entity_id = wi.entity_id
    JOIN instruments i
      ON i.entity_id = e.entity_id
    WHERE e.active = true
      AND i.symbol IS NOT NULL
),
combined AS
(
    SELECT
        user_id,
        entity_id,
        instrument_id,
        symbol,
        name,
        exchange,
        currency_code,
        instrument_type,
        quantity,
        market_value,
        true AS in_portfolio,
        false AS in_watchlist
    FROM portfolio_rows

    UNION ALL

    SELECT
        user_id,
        entity_id,
        instrument_id,
        symbol,
        name,
        exchange,
        currency_code,
        instrument_type,
        0::numeric,
        0::numeric,
        false,
        true
    FROM watchlist_rows
),
aggregated AS
(
    SELECT
        user_id,
        entity_id,
        instrument_id,
        symbol,
        name,
        exchange,
        currency_code,
        instrument_type,
        SUM(quantity)::numeric AS quantity,
        SUM(market_value)::numeric AS market_value,
        BOOL_OR(in_portfolio) AS in_portfolio,
        BOOL_OR(in_watchlist) AS in_watchlist
    FROM combined
    GROUP BY
        user_id,
        entity_id,
        instrument_id,
        symbol,
        name,
        exchange,
        currency_code,
        instrument_type
),
totals AS
(
    SELECT
        user_id,
        NULLIF(
            SUM(market_value),
            0
        ) AS total_value
    FROM aggregated
    GROUP BY user_id
)
SELECT
    a.user_id,
    a.entity_id,
    a.instrument_id,
    a.symbol,
    a.name,
    a.exchange,
    a.currency_code,
    a.instrument_type,
    a.quantity,
    a.market_value,
    CASE
        WHEN t.total_value IS NULL
            THEN 0
        ELSE ROUND(
            a.market_value
            / t.total_value
            * 100,
            4
        )
    END AS allocation_pct,
    a.in_portfolio,
    a.in_watchlist
FROM aggregated a
JOIN totals t
  ON t.user_id = a.user_id
ORDER BY
    a.user_id,
    a.symbol,
    a.exchange NULLS LAST;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_intelligence_entity_candidates() RETURNS TABLE(user_id uuid, entity_id uuid, instrument_id uuid, symbol character varying, name character varying, exchange character varying, currency_code character, instrument_type character varying, quantity numeric, market_value numeric, allocation_pct numeric, in_portfolio boolean, in_watchlist boolean, related_symbols jsonb)
    LANGUAGE sql STABLE
    AS $$
WITH user_entities AS (
    SELECT
        pf.user_id,
        i.entity_id,
        sum(p.quantity)::numeric AS quantity,
        sum(
            coalesce(
                public.fx_convert(
                    p.quantity * i.last_price,
                    i.currency_code,
                    u.reporting_currency
                ),
                0
            )
        )::numeric AS market_value,
        true AS in_portfolio,
        false AS in_watchlist
    FROM public.positions p
    JOIN public.portfolios pf
      ON pf.portfolio_id = p.portfolio_id
    JOIN public.instruments i
      ON i.instrument_id = p.instrument_id
    JOIN public.entities e
      ON e.entity_id = i.entity_id
    JOIN public.users u
      ON u.user_id = pf.user_id
     AND u.is_active = true
    WHERE e.active = true
    GROUP BY
        pf.user_id,
        i.entity_id

    UNION ALL

    SELECT DISTINCT
        w.user_id,
        wi.entity_id,
        0::numeric AS quantity,
        0::numeric AS market_value,
        false AS in_portfolio,
        true AS in_watchlist
    FROM public.watchlist_items wi
    JOIN public.watchlists w
      ON w.watchlist_id = wi.watchlist_id
    JOIN public.users u
      ON u.user_id = w.user_id
     AND u.is_active = true
    JOIN public.entities e
      ON e.entity_id = wi.entity_id
    WHERE e.active = true
),
aggregated AS (
    SELECT
        ue.user_id,
        ue.entity_id,
        sum(ue.quantity)::numeric AS quantity,
        sum(ue.market_value)::numeric AS market_value,
        bool_or(ue.in_portfolio) AS in_portfolio,
        bool_or(ue.in_watchlist) AS in_watchlist
    FROM user_entities ue
    GROUP BY
        ue.user_id,
        ue.entity_id
),
representative AS (
    SELECT DISTINCT ON (
        a.user_id,
        a.entity_id
    )
        a.user_id,
        a.entity_id,
        i.instrument_id,
        i.symbol,
        i.exchange,
        i.currency_code,
        i.instrument_type
    FROM aggregated a
    JOIN public.instruments i
      ON i.entity_id = a.entity_id
    LEFT JOIN public.positions p
      ON p.instrument_id = i.instrument_id
    LEFT JOIN public.portfolios pf
      ON pf.portfolio_id = p.portfolio_id
     AND pf.user_id = a.user_id
    WHERE NULLIF(btrim(i.symbol), '') IS NOT NULL
    ORDER BY
        a.user_id,
        a.entity_id,
        (pf.portfolio_id IS NOT NULL) DESC,
        i.price_as_of DESC NULLS LAST,
        i.instrument_id
),
totals AS (
    SELECT
        a.user_id,
        nullif(sum(a.market_value), 0) AS total_value
    FROM aggregated a
    GROUP BY a.user_id
)
SELECT
    a.user_id,
    a.entity_id,
    r.instrument_id,
    r.symbol,
    e.name,
    r.exchange,
    r.currency_code,
    r.instrument_type,
    a.quantity,
    a.market_value,
    CASE
        WHEN t.total_value IS NULL THEN 0
        ELSE round(
            a.market_value / t.total_value * 100,
            4
        )
    END AS allocation_pct,
    a.in_portfolio,
    a.in_watchlist,
    (
        SELECT jsonb_agg(
            jsonb_build_object(
                'instrument_id', i.instrument_id,
                'symbol', i.symbol,
                'exchange', i.exchange,
                'currency', i.currency_code
            )
            ORDER BY
                i.symbol,
                i.exchange NULLS LAST
        )
        FROM public.instruments i
        WHERE i.entity_id = a.entity_id
    ) AS related_symbols
FROM aggregated a
JOIN representative r
  ON r.user_id = a.user_id
 AND r.entity_id = a.entity_id
JOIN public.entities e
  ON e.entity_id = a.entity_id
JOIN totals t
  ON t.user_id = a.user_id
ORDER BY
    a.user_id,
    e.name,
    a.entity_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_watchlist_items(p_watchlist_id uuid, p_user_id uuid) RETURNS TABLE(watchlist_item_id uuid, watchlist_id uuid, entity_id uuid, priority smallint, target_price numeric, notes text, name character varying, instrument_id uuid, symbol character varying, exchange character varying, currency_code character, last_price numeric)
    LANGUAGE sql STABLE
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
LEFT JOIN LATERAL (
    SELECT listing.*
    FROM instruments listing
    WHERE listing.entity_id = e.entity_id
    ORDER BY listing.instrument_id
    LIMIT 1
) i ON true
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

CREATE OR REPLACE FUNCTION public.sp_get_watchlist_items_with_pricing(p_watchlist_id uuid, p_user_id uuid, p_max_age_hours numeric DEFAULT 96) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT
        to_jsonb(w)
        || jsonb_build_object(
            'instrument_id', i.instrument_id,
            'currency_code', i.currency_code,
            'price_as_of', i.price_as_of,
            'price_age_hours',
                CASE
                    WHEN i.price_as_of IS NULL THEN NULL
                    ELSE EXTRACT(EPOCH FROM (now() - i.price_as_of)) / 3600.0
                END,
            'price_is_stale',
                CASE
                    WHEN w.last_price IS NULL OR i.price_as_of IS NULL THEN NULL
                    ELSE i.price_as_of < now() - make_interval(
                        secs => (p_max_age_hours * 3600)::double precision
                    )
                END,
            'price_refresh_status',
                COALESCE(
                    q.status,
                    CASE
                        WHEN w.last_price IS NOT NULL THEN 'COMPLETED'
                        ELSE 'UNAVAILABLE'
                    END
                ),
            'price_refresh_attempt_count', q.attempt_count,
            'price_refresh_max_attempts', q.max_attempts,
            'price_refresh_next_attempt_at', q.next_attempt_at,
            'price_refresh_error', q.last_error
        )
    FROM public.sp_get_watchlist_items(p_watchlist_id, p_user_id) w
    LEFT JOIN public.instruments i
            ON i.instrument_id = w.instrument_id
    LEFT JOIN public.market_price_refresh_queue q
      ON q.instrument_id = i.instrument_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_positions_with_pricing(p_portfolio_id uuid, p_user_id uuid, p_max_age_hours numeric DEFAULT 96) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT
        to_jsonb(p)
        || jsonb_build_object(
            'cost_basis_amount', pos.cost_basis_amount,
            'cost_basis_currency', pos.cost_basis_currency,
            'price_as_of', i.price_as_of,
            'price_age_hours',
                CASE
                    WHEN i.price_as_of IS NULL THEN NULL
                    ELSE EXTRACT(EPOCH FROM (now() - i.price_as_of)) / 3600.0
                END,
            'price_is_stale',
                CASE
                    WHEN i.last_price IS NULL OR i.price_as_of IS NULL THEN NULL
                    ELSE i.price_as_of < now() - make_interval(
                        secs => (p_max_age_hours * 3600)::double precision
                    )
                END,
            'price_refresh_status',
                COALESCE(
                    q.status,
                    CASE
                        WHEN i.last_price IS NOT NULL THEN 'COMPLETED'
                        ELSE 'UNAVAILABLE'
                    END
                ),
            'price_refresh_attempt_count', q.attempt_count,
            'price_refresh_max_attempts', q.max_attempts,
            'price_refresh_next_attempt_at', q.next_attempt_at,
            'price_refresh_error', q.last_error
        )
    FROM public.sp_get_positions(p_portfolio_id, p_user_id) p
    JOIN public.positions pos ON pos.position_id = p.position_id
    JOIN public.instruments i
      ON i.instrument_id = p.instrument_id
    LEFT JOIN public.market_price_refresh_queue q
      ON q.instrument_id = p.instrument_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_portfolio_summary(p_portfolio_id uuid, p_user_id uuid) RETURNS TABLE(portfolio_currency character, reporting_currency character, invested_value_native numeric, portfolio_value_native numeric, profit_loss_native numeric, invested_value_reporting numeric, portfolio_value_reporting numeric, profit_loss_reporting numeric, profit_loss_pct numeric, positions_count bigint)
    LANGUAGE sql STABLE
    AS $$
WITH portfolio_data AS (
    SELECT
        pf.base_currency,
        u.reporting_currency,
        COUNT(pos.position_id) AS positions_count,
        CASE WHEN COUNT(pos.position_id) = COUNT(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, pf.base_currency
        )) THEN COALESCE(SUM(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, pf.base_currency
        )), 0) ELSE NULL END::numeric AS invested_value_native,
        CASE WHEN COUNT(pos.position_id) = COUNT(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, u.reporting_currency
        )) THEN COALESCE(SUM(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, u.reporting_currency
        )), 0) ELSE NULL END::numeric AS invested_value_reporting,
        COALESCE(
            SUM(
                CASE
                    WHEN pos.position_id IS NULL
                      OR i.last_price IS NULL
                      OR NULLIF(BTRIM(i.currency_code), '') IS NULL
                    THEN 0
                    ELSE public.fx_convert(
                        pos.quantity * i.last_price,
                        i.currency_code,
                        pf.base_currency
                    )
                END
            ),
            0
        )::numeric AS portfolio_value_native
    FROM public.portfolios pf
    JOIN public.users u
      ON u.user_id = pf.user_id
    LEFT JOIN public.positions pos
      ON pos.portfolio_id = pf.portfolio_id
    LEFT JOIN public.instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE pf.portfolio_id = p_portfolio_id
      AND pf.user_id = p_user_id
    GROUP BY
        pf.base_currency,
        u.reporting_currency
),
totals AS (
    SELECT
        base_currency,
        reporting_currency,
        invested_value_native,
        invested_value_reporting,
        portfolio_value_native,
        portfolio_value_native - invested_value_native AS profit_loss_native,
        positions_count
    FROM portfolio_data
)
SELECT
    base_currency,
    reporting_currency,
    invested_value_native,
    portfolio_value_native,
    profit_loss_native,
    invested_value_reporting,
    CASE
        WHEN NULLIF(BTRIM(base_currency), '') IS NULL
          OR NULLIF(BTRIM(reporting_currency), '') IS NULL
        THEN NULL
        ELSE public.fx_convert(
            portfolio_value_native,
            base_currency,
            reporting_currency
        )
    END,
    CASE
        WHEN NULLIF(BTRIM(base_currency), '') IS NULL
          OR NULLIF(BTRIM(reporting_currency), '') IS NULL
        THEN NULL
        ELSE public.fx_convert(
            portfolio_value_native,
            base_currency,
            reporting_currency
        ) - invested_value_reporting
    END,
    CASE
        WHEN invested_value_native > 0
        THEN (profit_loss_native / invested_value_native) * 100
        ELSE NULL
    END,
    positions_count
FROM totals;
$$;

CREATE OR REPLACE FUNCTION public.sp_get_portfolios(p_user_id uuid) RETURNS TABLE(portfolio_id uuid, user_id uuid, name character varying, portfolio_type character varying, description text, base_currency character, invested_value numeric, portfolio_value numeric, profit_loss numeric, profit_loss_pct numeric, positions_count bigint, created_at timestamp with time zone, updated_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
SELECT
    pf.portfolio_id,
    pf.user_id,
    pf.name,
    pf.portfolio_type::varchar,
    pf.description,
    pf.base_currency,
    totals.invested_value,
    totals.portfolio_value,
    totals.portfolio_value - totals.invested_value AS profit_loss,
    CASE
        WHEN totals.invested_value > 0
        THEN ((totals.portfolio_value - totals.invested_value)
              / totals.invested_value) * 100
        ELSE NULL
    END AS profit_loss_pct,
    totals.positions_count,
    pf.created_at,
    pf.updated_at
FROM portfolios pf
LEFT JOIN LATERAL (
    SELECT
        COUNT(pos.position_id) AS positions_count,
        CASE WHEN COUNT(pos.position_id) = COUNT(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, pf.base_currency
        )) THEN COALESCE(SUM(public.position_cost_basis(
            pos.quantity, pos.avg_price, i.currency_code,
            pos.cost_basis_amount, pos.cost_basis_currency, pf.base_currency
        )), 0) ELSE NULL END::numeric AS invested_value,
        COALESCE(SUM(
            CASE
                WHEN i.last_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * i.last_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS portfolio_value
    FROM positions pos
    JOIN instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE pos.portfolio_id = pf.portfolio_id
) totals ON true
WHERE pf.user_id = p_user_id
ORDER BY pf.name;
$$;

CREATE OR REPLACE FUNCTION public.sp_upsert_position(p_position_id uuid, p_portfolio_id uuid, p_user_id uuid, p_instrument_id uuid, p_quantity numeric, p_avg_price numeric, p_opened_at timestamp with time zone, p_notes text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_position_id uuid;
    v_currency_code char(3);
BEGIN
    IF p_portfolio_id IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.portfolios pf
           WHERE pf.portfolio_id = p_portfolio_id
             AND pf.user_id = p_user_id
       )
    THEN
        RAISE EXCEPTION
            'Portfolio not found or not accessible: %',
            p_portfolio_id
            USING ERRCODE = '23503';
    END IF;

    SELECT i.currency_code
    INTO v_currency_code
    FROM public.instruments i
    WHERE i.instrument_id = p_instrument_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Instrument not found: %',
            p_instrument_id
            USING ERRCODE = '23503';
    END IF;

    IF NULLIF(BTRIM(v_currency_code), '') IS NULL THEN
        RAISE EXCEPTION
            'Instrument % has no currency',
            p_instrument_id
            USING ERRCODE = '23514';
    END IF;

    IF p_position_id IS NULL THEN
        INSERT INTO public.positions
        (
            portfolio_id,
            instrument_id,
            quantity,
            avg_price,
            opened_at,
            notes
        )
        VALUES
        (
            p_portfolio_id,
            p_instrument_id,
            p_quantity,
            p_avg_price,
            p_opened_at,
            p_notes
        )
        RETURNING position_id
        INTO v_position_id;
    ELSE
        UPDATE public.positions
        SET
                        cost_basis_amount = CASE
                                WHEN quantity IS DISTINCT FROM p_quantity
                                    OR avg_price IS DISTINCT FROM p_avg_price
                                    OR instrument_id IS DISTINCT FROM p_instrument_id
                                THEN NULL ELSE cost_basis_amount END,
                        cost_basis_currency = CASE
                                WHEN quantity IS DISTINCT FROM p_quantity
                                    OR avg_price IS DISTINCT FROM p_avg_price
                                    OR instrument_id IS DISTINCT FROM p_instrument_id
                                THEN NULL ELSE cost_basis_currency END,
            instrument_id = p_instrument_id,
            quantity = p_quantity,
            avg_price = p_avg_price,
            opened_at = p_opened_at,
            notes = p_notes
        WHERE position_id = p_position_id
          AND portfolio_id = p_portfolio_id
        RETURNING position_id
        INTO v_position_id;

        IF v_position_id IS NULL THEN
            RAISE EXCEPTION
                'Position % not found in portfolio %',
                p_position_id,
                p_portfolio_id
                USING ERRCODE = '23503';
        END IF;
    END IF;

    RETURN v_position_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_enqueue_market_price_refresh(p_instrument_id uuid, p_symbol character varying, p_data_source character varying) RETURNS uuid
    LANGUAGE sql
    AS $$
    INSERT INTO market_price_refresh_queue
    (
        instrument_id,
        symbol,
        data_source,
        status,
        attempt_count,
        next_attempt_at,
        locked_at,
        locked_by,
        last_error,
        completed_at,
        updated_at
    )
    VALUES
    (
        p_instrument_id,
        UPPER(TRIM(p_symbol)),
        COALESCE(
            NULLIF(
                UPPER(TRIM(p_data_source)),
                ''
            ),
            'YAHOO'
        ),
        'PENDING',
        0,
        now(),
        NULL,
        NULL,
        NULL,
        NULL,
        now()
    )
    ON CONFLICT
    (
        instrument_id
    )
    DO UPDATE SET
        symbol =
            EXCLUDED.symbol,

        data_source =
            EXCLUDED.data_source,

        status =
            CASE
                WHEN
                    market_price_refresh_queue.status IN ('PENDING', 'PROCESSING', 'RETRY')
                THEN
                    market_price_refresh_queue.status
                ELSE
                    'PENDING'
            END,

        attempt_count =
            CASE
                WHEN
                    market_price_refresh_queue.status IN ('PENDING', 'PROCESSING', 'RETRY')
                THEN
                    market_price_refresh_queue.attempt_count
                ELSE
                    0
            END,

        next_attempt_at =
            CASE
                WHEN
                    market_price_refresh_queue.status IN ('PENDING', 'PROCESSING', 'RETRY')
                THEN
                    market_price_refresh_queue.next_attempt_at
                ELSE
                    now()
            END,

        locked_at =
            CASE
                WHEN
                    market_price_refresh_queue.status =
                    'PROCESSING'
                THEN
                    market_price_refresh_queue.locked_at
                ELSE
                    NULL
            END,

        locked_by =
            CASE
                WHEN
                    market_price_refresh_queue.status =
                    'PROCESSING'
                THEN
                    market_price_refresh_queue.locked_by
                ELSE
                    NULL
            END,

        last_error = CASE
            WHEN market_price_refresh_queue.status IN ('PENDING', 'PROCESSING', 'RETRY')
            THEN market_price_refresh_queue.last_error
            ELSE NULL
        END,
        completed_at = NULL,
        updated_at = now()

    RETURNING
        market_price_refresh_queue.job_id
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_expire_evaluated_signals(p_scopes jsonb, p_signal_ids uuid[], p_stability_minutes integer DEFAULT 30) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_signal record;
    v_count integer := 0;
BEGIN
    IF jsonb_typeof(p_scopes) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Evaluation scopes must be an array';
    END IF;
    FOR v_signal IN
        SELECT s.*, evaluation.observed_at AS evaluation_time
        FROM public.trading_signals s
        JOIN (
            SELECT instrument_id, strategy_code, timeframe, max(observed_at) AS observed_at
            FROM jsonb_to_recordset(p_scopes) AS scope(
                instrument_id uuid, strategy_code varchar, timeframe varchar, observed_at timestamptz
            )
            GROUP BY instrument_id, strategy_code, timeframe
        ) evaluation USING (instrument_id, strategy_code, timeframe)
        WHERE s.status IN ('CANDIDATE', 'CONFIRMED')
          AND NOT (s.signal_id = ANY(COALESCE(p_signal_ids, ARRAY[]::uuid[])))
          AND s.observed_at < evaluation.observed_at - make_interval(mins => GREATEST(p_stability_minutes, 0))
        FOR UPDATE OF s
    LOOP
        INSERT INTO public.market_analysis_signal_events(
            signal_id, instrument_id, strategy_code, timeframe,
            previous_status, new_status, previous_direction, new_direction,
            event_type, reason, observed_at, payload
        ) VALUES (
            v_signal.signal_id, v_signal.instrument_id, v_signal.strategy_code, v_signal.timeframe,
            v_signal.status, 'EXPIRED', v_signal.direction, v_signal.direction,
            'EXPIRED', 'absent from successfully evaluated scope', v_signal.evaluation_time, '{}'::jsonb
        );
        UPDATE public.trading_signals SET status = 'EXPIRED'
        WHERE signal_id = v_signal.signal_id;
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_reconcile_signal_lifecycle(p_signal_id uuid, p_stability_minutes integer DEFAULT 30) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_signal record;
    v_previous record;
    v_generation record;
    v_closed record;
    v_closed_count integer := 0;
    v_status varchar;
    v_event_type varchar;
    v_event_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT signal.*
    INTO v_signal
    FROM public.trading_signals signal
    WHERE signal.signal_id = p_signal_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Signal not found: %', p_signal_id;
    END IF;

    IF v_signal.strategy_code = 'SWING_EXIT' THEN
        IF v_signal.status = 'CLOSED' THEN
            RETURN jsonb_build_object('signal_id', p_signal_id, 'status', 'CLOSED', 'event_type', 'UNCHANGED');
        END IF;
        FOR v_closed IN
            SELECT signal.* FROM public.trading_signals signal
            WHERE signal.instrument_id = v_signal.instrument_id
              AND signal.timeframe = v_signal.timeframe
              AND signal.direction = 'LONG'
              AND signal.status IN ('CANDIDATE', 'CONFIRMED')
              AND signal.observed_at <= v_signal.observed_at
            ORDER BY signal.signal_id
            FOR UPDATE
        LOOP
            INSERT INTO public.market_analysis_signal_events (
                signal_id, instrument_id, strategy_code, timeframe,
                previous_status, new_status, previous_direction, new_direction,
                event_type, observed_at, reason, payload
            ) VALUES (
                v_closed.signal_id, v_closed.instrument_id, v_closed.strategy_code, v_closed.timeframe,
                v_closed.status, 'CLOSED', v_closed.direction, v_closed.direction,
                'CLOSED', v_signal.observed_at, 'SWING_EXIT',
                jsonb_build_object('exit_signal_id', p_signal_id)
            );
            UPDATE public.trading_signals SET status = 'CLOSED' WHERE signal_id = v_closed.signal_id;
            v_closed_count := v_closed_count + 1;
        END LOOP;
        RETURN jsonb_build_object('signal_id', p_signal_id, 'status', 'CLOSED', 'event_type', 'CLOSED', 'closed_signals', v_closed_count);
    END IF;

    SELECT event.*
    INTO v_previous
    FROM public.market_analysis_signal_events event
    WHERE event.instrument_id = v_signal.instrument_id
      AND event.strategy_code = v_signal.strategy_code
      AND event.timeframe = v_signal.timeframe
    ORDER BY event.created_at DESC, event.signal_event_id DESC
    LIMIT 1;

    SELECT event.*
    INTO v_generation
    FROM public.market_analysis_signal_events event
    WHERE event.instrument_id = v_signal.instrument_id
      AND event.strategy_code = v_signal.strategy_code
      AND event.timeframe = v_signal.timeframe
      AND event.event_type IN ('CREATED', 'DIRECTION_CHANGED')
    ORDER BY event.created_at DESC, event.signal_event_id DESC
    LIMIT 1;

     IF v_previous.signal_event_id IS NULL
         OR (v_previous.new_status IN ('EXPIRED', 'CLOSED') AND v_signal.observed_at > v_previous.observed_at)
     THEN
        v_status := 'CANDIDATE';
        v_event_type := 'CREATED';

    ELSIF v_previous.new_direction IS DISTINCT FROM v_signal.direction THEN
        v_status := 'CANDIDATE';
        v_event_type := 'DIRECTION_CHANGED';

    ELSIF v_previous.new_status = 'CANDIDATE'
       AND v_generation.signal_event_id IS NOT NULL
       AND v_now >= (
            v_generation.created_at
            + make_interval(
                mins => GREATEST(p_stability_minutes, 0)
            )
       )
    THEN
        v_status := 'CONFIRMED';
        v_event_type := 'CONFIRMED';

    ELSE
        v_status := v_previous.new_status;
        v_event_type := 'OBSERVED';
    END IF;

    -- Repeating a scan over the same market bar must not create an unlimited
    -- number of identical OBSERVED events. A later call may still emit
    -- CONFIRMED because confirmation uses processing time (created_at), while
    -- observed_at remains the timestamp of the market observation.
    IF v_event_type = 'OBSERVED'
       AND v_previous.signal_id = v_signal.signal_id
       AND v_previous.observed_at = v_signal.observed_at
       AND v_previous.new_status = v_status
       AND v_previous.new_direction = v_signal.direction
    THEN
        UPDATE public.trading_signals
        SET status = v_status
        WHERE signal_id = v_signal.signal_id;

        RETURN jsonb_build_object(
            'signal_event_id', v_previous.signal_event_id,
            'signal_id', v_signal.signal_id,
            'status', v_status,
            'event_type', 'UNCHANGED'
        );
    END IF;

    INSERT INTO public.market_analysis_signal_events (
        signal_id,
        instrument_id,
        strategy_code,
        timeframe,
        previous_status,
        new_status,
        previous_direction,
        new_direction,
        event_type,
        observed_at,
        payload
    )
    VALUES (
        v_signal.signal_id,
        v_signal.instrument_id,
        v_signal.strategy_code,
        v_signal.timeframe,
        v_previous.new_status,
        v_status,
        v_previous.new_direction,
        v_signal.direction,
        v_event_type,
        v_signal.observed_at,
        jsonb_build_object(
            'confidence', v_signal.confidence,
            'reference_price', v_signal.reference_price,
            'market_observed_at', v_signal.observed_at,
            'processed_at', v_now
        )
    )
    RETURNING signal_event_id INTO v_event_id;

    UPDATE public.trading_signals
    SET status = v_status
    WHERE signal_id = v_signal.signal_id;

    RETURN jsonb_build_object(
        'signal_event_id', v_event_id,
        'signal_id', v_signal.signal_id,
        'status', v_status,
        'event_type', v_event_type
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_upsert_signal(p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_signal_id uuid;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'p_payload must be a JSON object';
    END IF;

    IF UPPER(BTRIM(p_payload->>'strategy_code')) = 'SWING_EXIT' THEN
        SELECT signal_id INTO v_signal_id FROM public.trading_signals
        WHERE instrument_id = (p_payload->>'instrument_id')::uuid
          AND strategy_code = 'SWING_EXIT'
          AND timeframe = UPPER(BTRIM(p_payload->>'timeframe'))
          AND observed_at = (p_payload->>'observed_at')::timestamptz
          AND status = 'CLOSED'
        ORDER BY signal_id LIMIT 1;
        IF FOUND THEN
            RETURN v_signal_id;
        END IF;
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
        composite_score,
        reference_price,
        invalidation_price,
        target_price,
        observed_at,
        evidence
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
        NULLIF(p_payload->'scores'->>'composite', '')::numeric,
        (p_payload->>'reference_price')::numeric,
        NULLIF(p_payload->>'invalidation_price', '')::numeric,
        NULLIF(p_payload->>'target_price', '')::numeric,
        (p_payload->>'observed_at')::timestamptz,
        COALESCE(p_payload->'evidence', '{}'::jsonb)
            || jsonb_build_object(
                'reasons',
                COALESCE(p_payload->'reasons', '[]'::jsonb)
            )
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
        llm_score = NULL,
        composite_score = EXCLUDED.composite_score,
        reference_price = EXCLUDED.reference_price,
        invalidation_price = EXCLUDED.invalidation_price,
        target_price = EXCLUDED.target_price,
        observed_at = EXCLUDED.observed_at,
        evidence = EXCLUDED.evidence,
        llm_analysis = NULL,
        enrichment_fingerprint = NULL
    WHERE (trading_signals.observed_at, trading_signals.evidence, trading_signals.reference_price,
           trading_signals.technical_score, trading_signals.financial_score, trading_signals.catalyst_score,
           trading_signals.invalidation_price, trading_signals.target_price, trading_signals.confidence,
           trading_signals.signal_type, trading_signals.trend, trading_signals.universe_id)
          IS DISTINCT FROM
          (EXCLUDED.observed_at, EXCLUDED.evidence, EXCLUDED.reference_price,
           EXCLUDED.technical_score, EXCLUDED.financial_score, EXCLUDED.catalyst_score,
           EXCLUDED.invalidation_price, EXCLUDED.target_price, EXCLUDED.confidence,
           EXCLUDED.signal_type, EXCLUDED.trend, EXCLUDED.universe_id)
    RETURNING signal_id INTO v_signal_id;

    IF v_signal_id IS NULL THEN
        SELECT signal_id INTO v_signal_id FROM public.trading_signals
        WHERE instrument_id = (p_payload->>'instrument_id')::uuid
          AND strategy_code = UPPER(BTRIM(p_payload->>'strategy_code'))
          AND timeframe = UPPER(BTRIM(p_payload->>'timeframe'))
          AND direction = UPPER(BTRIM(p_payload->>'direction'))
          AND status IN ('CANDIDATE', 'CONFIRMED');
    END IF;
    RETURN v_signal_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer, p_instrument_ids uuid[]) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE plpgsql STABLE
    AS $_$
BEGIN
    IF p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 500';
    END IF;

    IF NULLIF(BTRIM(p_enrichment_fingerprint), '') IS NULL THEN
        RAISE EXCEPTION 'p_enrichment_fingerprint cannot be empty';
    END IF;

    IF BTRIM(p_enrichment_fingerprint) !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION
            'p_enrichment_fingerprint must be a lowercase SHA-256 hexadecimal value';
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
            AND (p_instrument_ids IS NULL OR s.instrument_id = ANY(p_instrument_ids))
            AND s.strategy_code <> 'SWING_EXIT'
            AND COALESCE(s.evidence #> '{quant,llm_eligible}', 'true'::jsonb) = 'true'::jsonb
      AND (
          s.llm_analysis IS NULL
          OR s.enrichment_fingerprint IS DISTINCT FROM
             BTRIM(p_enrichment_fingerprint)
      )
    ORDER BY
        COALESCE(s.composite_score, s.technical_score, 0) DESC,
        s.observed_at DESC,
        s.signal_id
    LIMIT p_limit;
END;
$_$;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer DEFAULT 25) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT * FROM public.sp_market_analysis_list_pending_llm_enrichment(
        p_enrichment_fingerprint, p_limit, NULL::uuid[]
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_ranking_inputs(p_limit integer) RETURNS TABLE(signal_id uuid, instrument_id uuid, symbol character varying, direction character varying, origin character varying, attention_score numeric, technical_score numeric, quant_score numeric, llm_score numeric, verdict character varying, summary text, evidence jsonb, strategy_code character varying, timeframe character varying)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        s.signal_id,
        s.instrument_id,
        i.symbol,
        s.direction,
        coalesce(
            nullif(s.evidence #>> '{market_discovery,origin}', ''),
            CASE
                WHEN d.attention_score IS NOT NULL THEN 'DISCOVERY'
                ELSE 'MARKET_ANALYSIS'
            END
        )::character varying AS origin,
        coalesce(
            CASE
                WHEN jsonb_typeof(s.evidence #> '{market_discovery,attention_score}') = 'number'
                THEN (s.evidence #>> '{market_discovery,attention_score}')::numeric
            END,
            d.attention_score
        ) AS attention_score,
        s.technical_score,
        COALESCE(
            CASE WHEN jsonb_typeof(s.evidence #> '{quant,score}') = 'number'
                 THEN (s.evidence #>> '{quant,score}')::numeric END,
        CASE
            WHEN jsonb_typeof(s.evidence #> '{market_discovery,quant_score}') = 'number'
            THEN (s.evidence #>> '{market_discovery,quant_score}')::numeric
        END) AS quant_score,
        s.llm_score,
        nullif(s.llm_analysis ->> 'verdict', '')::character varying AS verdict,
        nullif(s.llm_analysis ->> 'summary', '')::text AS summary,
        coalesce(s.evidence, '{}'::jsonb) AS evidence,
        s.strategy_code,
        s.timeframe
    FROM public.trading_signals s
    JOIN public.instruments i
      ON i.instrument_id = s.instrument_id
    LEFT JOIN LATERAL (
        SELECT max(c.attention_score) AS attention_score
        FROM public.market_discovery_candidates c
        WHERE c.instrument_id = s.instrument_id
    ) d ON true
    WHERE s.status IN ('CANDIDATE', 'CONFIRMED')
            AND s.strategy_code <> 'SWING_EXIT'
    ORDER BY s.observed_at DESC, s.signal_id
    LIMIT greatest(coalesce(p_limit, 200), 1);
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_build_candidates(p_snapshot_id uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_snapshot_id uuid;
    v_total integer;
    v_resolved integer;
    v_core integer;
    v_discovery integer;
    v_ambiguous integer;
    v_unresolved integer;
    v_rejected integer;
    v_summary jsonb;
    v_snapshot_results jsonb := '[]'::jsonb;
BEGIN
    IF p_snapshot_id IS NULL THEN
        FOR v_snapshot_id IN
            SELECT DISTINCT ON (source.source_type) s.snapshot_id
            FROM public.market_discovery_snapshots s
            JOIN public.market_discovery_sources source ON source.source_id = s.source_id
            WHERE source.is_enabled
            ORDER BY source.source_type, s.observed_at DESC, s.snapshot_id DESC
        LOOP
            v_summary := public.sp_market_discovery_build_candidates(v_snapshot_id);
            v_snapshot_results := v_snapshot_results || jsonb_build_array(v_summary);
        END LOOP;

        IF jsonb_array_length(v_snapshot_results) = 0 THEN
            RAISE EXCEPTION 'No market discovery snapshot is available';
        END IF;

        SELECT
            SUM((summary->>'total')::integer),
            SUM((summary->>'resolved')::integer),
            SUM((summary->>'core')::integer),
            SUM((summary->>'discovery')::integer),
            SUM((summary->>'ambiguous')::integer),
            SUM((summary->>'unresolved')::integer),
            SUM((summary->>'rejected')::integer)
        INTO v_total, v_resolved, v_core, v_discovery, v_ambiguous, v_unresolved, v_rejected
        FROM jsonb_array_elements(v_snapshot_results) AS results(summary);

        RETURN jsonb_build_object(
            'snapshots', v_snapshot_results,
            'total', v_total, 'resolved', v_resolved,
            'core', v_core, 'discovery', v_discovery,
            'ambiguous', v_ambiguous, 'unresolved', v_unresolved,
            'rejected', v_rejected
        );
    END IF;

    SELECT COALESCE(
        p_snapshot_id,
        (
            SELECT s.snapshot_id
            FROM public.market_discovery_snapshots s
            ORDER BY s.observed_at DESC, s.snapshot_id DESC
            LIMIT 1
        )
    )
    INTO v_snapshot_id;

    IF v_snapshot_id IS NULL THEN
        RAISE EXCEPTION 'No market discovery snapshot is available';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.market_discovery_snapshots s
        WHERE s.snapshot_id = v_snapshot_id
    ) THEN
        RAISE EXCEPTION 'Market discovery snapshot not found: %', v_snapshot_id;
    END IF;

    DELETE FROM public.market_discovery_candidates c
    WHERE c.snapshot_id = v_snapshot_id;

    WITH snapshot_context AS (
        SELECT s.snapshot_id, GREATEST(s.returned_count, 1) AS returned_count,
               source.source_type, source.provider_code
        FROM public.market_discovery_snapshots s
        JOIN public.market_discovery_sources source ON source.source_id = s.source_id
        WHERE s.snapshot_id = v_snapshot_id
    ),
    item_scores AS (
        SELECT
            i.*,
            sc.source_type,
            sc.provider_code,
            LEAST(
                CASE WHEN sc.source_type IN ('GAINERS', 'LOSERS', 'MOST_ACTIVE')
                    THEN 100.0 ELSE 35.0 END,
                GREATEST(
                    0.0,
                    CASE WHEN sc.source_type IN ('GAINERS', 'LOSERS', 'MOST_ACTIVE')
                        THEN 100.0 ELSE 35.0 END * (
                        1.0 - (
                            GREATEST(COALESCE(i.rank, sc.returned_count), 1) - 1
                        )::numeric / GREATEST(sc.returned_count - 1, 1)
                    )
                )
            ) AS rank_score,
            LEAST(
                30.0,
                15.0 * LEAST(
                    1.0,
                    LN(1 + GREATEST(COALESCE(i.mentions, 0) - COALESCE(i.previous_mentions, 0), 0)) / LN(51.0)
                )
                + 15.0 * LEAST(
                    1.0,
                    GREATEST(COALESCE(i.mentions, 0) - COALESCE(i.previous_mentions, 0), 0)::numeric
                    / GREATEST(COALESCE(i.previous_mentions, 0), 1)
                )
            ) AS mentions_growth_score,
            LEAST(
                20.0,
                20.0 * LEAST(
                    1.0,
                    LN(
                        1 + GREATEST(
                            COALESCE(i.previous_rank, i.rank) - COALESCE(i.rank, i.previous_rank),
                            0
                        )
                    ) / LN(101.0)
                )
            ) AS rank_momentum_score,
            LEAST(
                15.0,
                15.0 * LEAST(
                    1.0,
                    LN(1 + GREATEST(COALESCE(i.upvotes, 0), 0)) / LN(1001.0)
                )
            ) AS engagement_score
        FROM public.market_discovery_snapshot_items i
        JOIN snapshot_context sc ON sc.snapshot_id = i.snapshot_id
    ),
    exact_matches AS (
        SELECT
            scores.snapshot_item_id,
            i.instrument_id,
            i.entity_id,
            e.active AS instrument_active,
            EXISTS (
                SELECT 1
                FROM public.market_universe_members um
                JOIN public.market_universes u
                  ON u.universe_id = um.universe_id
                WHERE um.instrument_id = i.instrument_id
                  AND um.valid_to IS NULL
                  AND u.active = true
            ) AS is_core_listing
        FROM item_scores scores
        JOIN public.instruments i
          ON upper(btrim(i.symbol)) =
             upper(btrim(scores.symbol))
        JOIN public.entities e
          ON e.entity_id = i.entity_id
    ),
    match_summary AS (
        SELECT
            scores.snapshot_item_id,
            COUNT(matches.instrument_id) AS all_instrument_count,
            COUNT(matches.instrument_id) FILTER (WHERE matches.instrument_active) AS active_instrument_count,
            COUNT(DISTINCT matches.entity_id) FILTER (WHERE matches.instrument_active) AS active_entity_count
        FROM item_scores scores
        LEFT JOIN exact_matches matches ON matches.snapshot_item_id = scores.snapshot_item_id
        GROUP BY scores.snapshot_item_id
    ),
    selected_match AS (
        SELECT DISTINCT ON (matches.snapshot_item_id)
            matches.snapshot_item_id,
            matches.instrument_id,
            matches.entity_id,
            matches.is_core_listing
        FROM exact_matches matches
        JOIN match_summary summary ON summary.snapshot_item_id = matches.snapshot_item_id
        WHERE matches.instrument_active
          AND summary.active_entity_count = 1
        ORDER BY
            matches.snapshot_item_id,
            matches.is_core_listing DESC,
            matches.instrument_id
    ),
    universe_memberships AS (
        SELECT
            selected.snapshot_item_id,
            COALESCE(
                jsonb_agg(DISTINCT u.code ORDER BY u.code)
                    FILTER (WHERE u.code IS NOT NULL),
                '[]'::jsonb
            ) AS universe_codes
        FROM selected_match selected
        LEFT JOIN public.market_universe_members um
          ON um.instrument_id = selected.instrument_id
         AND um.valid_to IS NULL
        LEFT JOIN public.market_universes u
          ON u.universe_id = um.universe_id
         AND u.active = true
        GROUP BY selected.snapshot_item_id
    )
    INSERT INTO public.market_discovery_candidates (
        snapshot_id,
        snapshot_item_id,
        entity_id,
        instrument_id,
        symbol,
        origin,
        resolution_status,
        attention_score,
        universe_codes,
        attention_evidence
    )
    SELECT
        scores.snapshot_id,
        scores.snapshot_item_id,
        CASE WHEN summary.active_entity_count = 1 THEN selected.entity_id END,
        CASE WHEN summary.active_entity_count = 1 THEN selected.instrument_id END,
        upper(btrim(scores.symbol)),
        CASE
            WHEN summary.active_entity_count = 1 AND COALESCE(jsonb_array_length(memberships.universe_codes), 0) > 0
                THEN 'CORE'
            WHEN summary.active_entity_count = 1
                THEN 'DISCOVERY'
            ELSE NULL
        END,
        CASE
            WHEN summary.all_instrument_count = 0 THEN 'UNRESOLVED'
            WHEN summary.active_instrument_count = 0 THEN 'REJECTED'
            WHEN summary.active_entity_count > 1 THEN 'AMBIGUOUS'
            WHEN summary.active_entity_count = 1 THEN 'RESOLVED'
            ELSE 'UNRESOLVED'
        END,
        ROUND(
            (
                LEAST(
                    100.0,
                    GREATEST(
                        0.0,
                        scores.rank_score
                        + scores.mentions_growth_score
                        + scores.rank_momentum_score
                        + scores.engagement_score
                    )
                )
            )::numeric,
            4
        ),
        COALESCE(memberships.universe_codes, '[]'::jsonb),
        jsonb_build_object(
            'source_type', scores.source_type,
            'provider_code', scores.provider_code,
            'source_score', scores.score,
            'source_payload', scores.raw_payload,
            'rank', scores.rank,
            'previous_rank', scores.previous_rank,
            'rank_improvement', CASE
                WHEN scores.rank IS NOT NULL AND scores.previous_rank IS NOT NULL
                    THEN scores.previous_rank - scores.rank
                ELSE NULL
            END,
            'mentions', scores.mentions,
            'previous_mentions', scores.previous_mentions,
            'mentions_delta', COALESCE(scores.mentions, 0) - COALESCE(scores.previous_mentions, 0),
            'upvotes', scores.upvotes,
            'components', jsonb_build_object(
                'rank_score',
                    ROUND(scores.rank_score::numeric, 4),
                'mentions_growth_score',
                    ROUND(scores.mentions_growth_score::numeric, 4),
                'rank_momentum_score',
                    ROUND(scores.rank_momentum_score::numeric, 4),
                'engagement_score',
                    ROUND(scores.engagement_score::numeric, 4)
            ),
            'resolution', jsonb_build_object(
                'all_instrument_count', summary.all_instrument_count,
                'active_instrument_count', summary.active_instrument_count,
                'active_entity_count', summary.active_entity_count,
                'selected_instrument_id', selected.instrument_id,
                'selected_entity_id', selected.entity_id,
                'selection_rule', 'exact symbol; unique active entity; active universe listing preferred; instrument_id tie-break'
            )
        )
    FROM item_scores scores
    JOIN match_summary summary ON summary.snapshot_item_id = scores.snapshot_item_id
    LEFT JOIN selected_match selected ON selected.snapshot_item_id = scores.snapshot_item_id
    LEFT JOIN universe_memberships memberships ON memberships.snapshot_item_id = scores.snapshot_item_id;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE resolution_status = 'RESOLVED'),
        COUNT(*) FILTER (WHERE origin = 'CORE'),
        COUNT(*) FILTER (WHERE origin = 'DISCOVERY'),
        COUNT(*) FILTER (WHERE resolution_status = 'AMBIGUOUS'),
        COUNT(*) FILTER (WHERE resolution_status = 'UNRESOLVED'),
        COUNT(*) FILTER (WHERE resolution_status = 'REJECTED')
    INTO
        v_total,
        v_resolved,
        v_core,
        v_discovery,
        v_ambiguous,
        v_unresolved,
        v_rejected
    FROM public.market_discovery_candidates c
    WHERE c.snapshot_id = v_snapshot_id;

    RETURN jsonb_build_object(
        'snapshot_id', v_snapshot_id,
        'total', v_total,
        'resolved', v_resolved,
        'core', v_core,
        'discovery', v_discovery,
        'ambiguous', v_ambiguous,
        'unresolved', v_unresolved,
        'rejected', v_rejected
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_list_scan_candidates(p_snapshot_id uuid DEFAULT NULL::uuid, p_core_min_attention_score numeric DEFAULT 20, p_discovery_min_attention_score numeric DEFAULT 40, p_core_limit integer DEFAULT 100, p_discovery_limit integer DEFAULT 25) RETURNS TABLE(candidate_id uuid, snapshot_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange text, currency_code text, isin text, universe_id uuid, origin character varying, attention_score numeric, universe_codes jsonb, attention_evidence jsonb)
    LANGUAGE sql STABLE
    AS $$
WITH latest_snapshots AS (
    SELECT DISTINCT ON (source.source_type) s.snapshot_id
    FROM public.market_discovery_snapshots s
    JOIN public.market_discovery_sources source ON source.source_id = s.source_id
    WHERE source.is_enabled
    ORDER BY source.source_type, s.observed_at DESC, s.snapshot_id DESC
),
selected_snapshots AS (
    SELECT p_snapshot_id AS snapshot_id WHERE p_snapshot_id IS NOT NULL
    UNION ALL
    SELECT snapshot_id FROM latest_snapshots WHERE p_snapshot_id IS NULL
),
eligible_candidates AS (
    SELECT c.*
    FROM public.market_discovery_candidates c
    JOIN selected_snapshots selected
      ON selected.snapshot_id = c.snapshot_id
    WHERE c.resolution_status = 'RESOLVED'
      AND (
          (c.origin = 'CORE' AND c.attention_score >= COALESCE(p_core_min_attention_score, 0))
          OR
          (c.origin = 'DISCOVERY' AND c.attention_score >= COALESCE(p_discovery_min_attention_score, 0))
      )
),
deduplicated AS (
    SELECT DISTINCT ON (instrument_id)
        *
    FROM eligible_candidates
    ORDER BY
        instrument_id,
        CASE origin WHEN 'CORE' THEN 0 ELSE 1 END,
        attention_score DESC,
        symbol,
        candidate_id
),
eligible AS (
    SELECT d.*,
        ROW_NUMBER() OVER (
            PARTITION BY d.origin
            ORDER BY d.attention_score DESC, d.symbol, d.instrument_id
        ) AS origin_position
    FROM deduplicated d
),
limited AS (
    SELECT *
    FROM eligible
    WHERE (origin = 'CORE' AND origin_position <= GREATEST(p_core_limit, 0))
       OR (origin = 'DISCOVERY' AND origin_position <= GREATEST(p_discovery_limit, 0))
),
preferred_universe AS (
    SELECT
        d.candidate_id,
        membership.universe_id
    FROM limited d
    LEFT JOIN LATERAL (
        SELECT um.universe_id
        FROM public.market_universe_members um
        JOIN public.market_universes u
          ON u.universe_id = um.universe_id
        WHERE um.instrument_id = d.instrument_id
          AND um.valid_to IS NULL
          AND u.active = true
        ORDER BY u.code, um.universe_id
        LIMIT 1
    ) membership ON true
)
SELECT
    d.candidate_id,
    d.snapshot_id,
    d.instrument_id,
    d.entity_id,
    d.symbol,
    i.exchange,
    i.currency_code,
    NULL::text AS isin,
    preferred.universe_id,
    d.origin,
    d.attention_score,
    d.universe_codes,
    d.attention_evidence
FROM limited d
JOIN public.instruments i
  ON i.instrument_id = d.instrument_id
LEFT JOIN preferred_universe preferred
  ON preferred.candidate_id = d.candidate_id
ORDER BY
    CASE d.origin WHEN 'CORE' THEN 0 ELSE 1 END,
    d.attention_score DESC,
    d.symbol;
$$;
COMMIT;
