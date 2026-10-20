--
-- PostgreSQL database dump
--

\restrict 6fNxrOEk96Qv79xD2Aypmf84MYF65NxqQYlDZKQUIpKLz43nlz3se8C0FwQTtVq

-- Dumped from database version 18.6 (Debian 18.6-1.pgdg13+2)
-- Dumped by pg_dump version 18.6 (Debian 18.6-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: entity_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.entity_type AS ENUM (
    'COMPANY',
    'ETF',
    'INDEX',
    'COMMODITY',
    'CRYPTO',
    'FUND',
    'CUSTOM'
);


--
-- Name: portfolio_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.portfolio_type AS ENUM (
    'PORTFOLIO',
    'SIMULATION'
);


--
-- Name: signal_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.signal_type AS ENUM (
    'STRONG_BUY',
    'BUY',
    'NEUTRAL',
    'SELL',
    'STRONG_SELL'
);


--
-- Name: trend_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.trend_type AS ENUM (
    'STRONG_BULLISH',
    'BULLISH',
    'STRONG_BEARISH',
    'BEARISH',
    'SIDEWAYS'
);


--
-- Name: fx_convert(numeric, character, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fx_convert(p_amount numeric, p_from character, p_to character) RETURNS numeric
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_from_currency char(3);
    v_to_currency char(3);
    v_from_rate numeric;
    v_to_rate numeric;
BEGIN
    IF p_amount IS NULL THEN
        RETURN NULL;
    END IF;

    v_from_currency := UPPER(TRIM(p_from));
    v_to_currency := UPPER(TRIM(p_to));

    IF v_from_currency = v_to_currency THEN
        RETURN p_amount;
    END IF;

    SELECT er.rate_to_usd
    INTO v_from_rate
    FROM exchange_rates er
    WHERE er.currency_code = v_from_currency;

    IF v_from_rate IS NULL THEN
        RAISE EXCEPTION
            'Exchange rate not found for %',
            v_from_currency;
    END IF;

    SELECT er.rate_to_usd
    INTO v_to_rate
    FROM exchange_rates er
    WHERE er.currency_code = v_to_currency;

    IF v_to_rate IS NULL THEN
        RAISE EXCEPTION
            'Exchange rate not found for %',
            v_to_currency;
    END IF;

    RETURN
        (p_amount * v_from_rate)
        / v_to_rate;
END;
$$;


--
-- Name: sp_claim_notifications(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_claim_notifications(p_limit integer) RETURNS TABLE(notification_id uuid, user_id uuid, source_id uuid, channel character varying, notification_key character varying, notification_type character varying, severity character varying, title text, message text, context jsonb, source_external_id text, source_config jsonb, attempt_count integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY

    WITH candidates AS
    (
        SELECT n.notification_id
        FROM notifications n

        JOIN user_sources us
          ON us.source_id = n.source_id
         AND us.user_id = n.user_id

        WHERE n.status IN (
                  'PENDING',
                  'RETRY'
              )

          AND n.source_id IS NOT NULL

          AND COALESCE(
                (us.config->>'enabled')::boolean,
                true
              ) = true

          AND COALESCE(
                n.next_attempt_at,
                now()
              ) <= now()

        ORDER BY n.created_at

        FOR UPDATE OF n SKIP LOCKED

        LIMIT GREATEST(
            p_limit,
            1
        )
    ),

    claimed AS
    (
        UPDATE notifications n
        SET
            status = 'PROCESSING',
            processing_started_at = now(),
            attempt_count =
                n.attempt_count + 1,
            last_error = NULL

        FROM candidates c

        WHERE n.notification_id =
              c.notification_id

        RETURNING n.*
    )

    SELECT
        c.notification_id,
        c.user_id,
        c.source_id,
        c.channel,
        c.notification_key,
        c.notification_type,
        c.severity,
        c.title,
        c.message,
        c.context,
        us.external_id,
        us.config,
        c.attempt_count

    FROM claimed c

    JOIN user_sources us
      ON us.source_id = c.source_id;
END;
$$;


--
-- Name: sp_cleanup_market_cache(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_cleanup_market_cache(p_days integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted INTEGER;
BEGIN

    DELETE
    FROM market_search_cache
    WHERE cached_at <
          NOW()
          - make_interval(
                days =>
                p_days
            );

    GET DIAGNOSTICS
        v_deleted = ROW_COUNT;

    RETURN v_deleted;

END;
$$;


--
-- Name: sp_complete_scheduler_job(uuid, character varying, text, jsonb, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_complete_scheduler_job(p_job_run_id uuid, p_status character varying, p_error_message text, p_result jsonb, p_completed_at timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE v_job_id uuid; v_status varchar;
BEGIN
    v_status := UPPER(TRIM(p_status));
    IF v_status NOT IN ('SUCCESS', 'PARTIAL', 'FAILED') THEN
        RAISE EXCEPTION 'Invalid scheduler status: %', p_status;
    END IF;
    UPDATE scheduler_job_runs
    SET completed_at=p_completed_at, status=v_status,
        error_message=p_error_message, result=p_result
    WHERE job_run_id=p_job_run_id RETURNING job_id INTO v_job_id;
    IF v_job_id IS NULL THEN RAISE EXCEPTION 'Scheduler run not found: %', p_job_run_id; END IF;
    UPDATE scheduler_jobs SET last_run_at=p_completed_at WHERE job_id=v_job_id;
END;
$$;


--
-- Name: sp_delete_portfolio(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_delete_portfolio(p_portfolio_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted INTEGER;
BEGIN

    DELETE
    FROM portfolios
    WHERE portfolio_id =
          p_portfolio_id
	AND user_id =
		p_user_id;

    GET DIAGNOSTICS
        v_deleted = ROW_COUNT;

    RETURN v_deleted > 0;

END;
$$;


--
-- Name: sp_delete_position(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_delete_position(p_position_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted INTEGER;
BEGIN

    DELETE
    FROM positions
    WHERE position_id =
          p_position_id
	AND portfolio_id IN (SELECT portfolio_id FROM portfolios WHERE user_id = p_user_id);

    GET DIAGNOSTICS
        v_deleted = ROW_COUNT;

    RETURN v_deleted > 0;

END;
$$;


--
-- Name: sp_delete_watchlist(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_delete_watchlist(p_watchlist_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted INTEGER;
BEGIN

    DELETE
    FROM watchlists
    WHERE watchlist_id =
          p_watchlist_id
	AND user_id =
		p_user_id;

    GET DIAGNOSTICS
        v_deleted = ROW_COUNT;

    RETURN v_deleted > 0;

END;
$$;


--
-- Name: sp_delete_watchlist_item(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_delete_watchlist_item(p_watchlist_item_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted INTEGER;
BEGIN

    DELETE
    FROM watchlist_items
    WHERE watchlist_item_id =
          p_watchlist_item_id
	AND watchlist_id IN (SELECT watchlist_id FROM watchlists WHERE user_id = p_user_id);		  

    GET DIAGNOSTICS
        v_deleted = ROW_COUNT;

    RETURN v_deleted > 0;

END;
$$;


--
-- Name: sp_enqueue_notification(uuid, uuid, character varying, character varying, character varying, uuid, character varying, character varying, character varying, text, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_enqueue_notification(p_user_id uuid, p_source_id uuid, p_channel character varying, p_notification_key character varying, p_notification_type character varying, p_entity_id uuid, p_symbol character varying, p_event_key character varying, p_severity character varying, p_title text, p_message text, p_context jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_notification_id uuid;
BEGIN
    IF NOT EXISTS
    (
        SELECT 1
        FROM user_sources us
        WHERE us.source_id = p_source_id
          AND us.user_id = p_user_id
          AND UPPER(us.source_type) =
              UPPER(p_channel)
          AND COALESCE(
                (us.config->>'enabled')::boolean,
                true
              ) = true
    )
    THEN
        RAISE EXCEPTION
            'Enabled source % for channel % '
            'does not belong to user %',
            p_source_id,
            p_channel,
            p_user_id;
    END IF;

    INSERT INTO notifications
    (
        user_id,
        source_id,
        channel,
        notification_key,
        notification_type,
        entity_id,
        symbol,
        event_key,
        severity,
        title,
        message,
        context,
        status,
        observed_at,
        attempt_count,
        next_attempt_at
    )
    VALUES
    (
        p_user_id,
        p_source_id,
        UPPER(p_channel),
        p_notification_key,
        p_notification_type,
        p_entity_id,
        p_symbol,
        p_event_key,
        p_severity,
        p_title,
        p_message,
        COALESCE(
            p_context,
            '{}'::jsonb
        ),
        'PENDING',
        now(),
        0,
        now()
    )
    ON CONFLICT
    (
        user_id,
        notification_key
    )
    WHERE notification_key IS NOT NULL
    DO NOTHING
    RETURNING notification_id
    INTO v_notification_id;

    RETURN v_notification_id;
END;
$$;


--
-- Name: sp_fail_scheduler_job(uuid, text, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_fail_scheduler_job(p_job_run_id uuid, p_error_message text, p_completed_at timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE v_job_id uuid;
BEGIN
    UPDATE scheduler_job_runs
    SET completed_at=p_completed_at, status='FAILED', error_message=p_error_message
    WHERE job_run_id=p_job_run_id RETURNING job_id INTO v_job_id;
    IF v_job_id IS NULL THEN RAISE EXCEPTION 'Scheduler run not found: %', p_job_run_id; END IF;
    UPDATE scheduler_jobs SET last_run_at=p_completed_at WHERE job_id=v_job_id;
END;
$$;


--
-- Name: sp_get_enabled_scheduler_jobs(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_enabled_scheduler_jobs() RETURNS TABLE(job_id uuid, job_name character varying, python_module character varying, trigger_type character varying, cron_expression character varying, interval_minutes integer)
    LANGUAGE sql STABLE
    AS $$
SELECT sj.job_id, sj.job_name, sj.python_module, sj.trigger_type,
       sj.cron_expression, sj.interval_minutes
FROM scheduler_jobs sj
WHERE sj.enabled = true
ORDER BY sj.job_name;
$$;


--
-- Name: sp_get_entities(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_entities(p_limit integer DEFAULT 500) RETURNS TABLE(entity_id uuid, name character varying, entity_type character varying, country_code character varying, symbol character varying, exchange character varying)
    LANGUAGE sql
    AS $$
SELECT
    e.entity_id,
    e.name,
    e.entity_type::varchar,
    e.country_code,

    i.symbol,
    i.exchange

FROM entities e

LEFT JOIN instruments i
ON i.entity_id =
   e.entity_id

WHERE e.active = TRUE

ORDER BY e.name

LIMIT p_limit;
$$;


--
-- Name: sp_get_entities_for_refresh(character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_entities_for_refresh(p_mode character varying DEFAULT 'MISSING'::character varying, p_symbol character varying DEFAULT NULL::character varying) RETURNS TABLE(entity_id uuid, instrument_id uuid, symbol character varying)
    LANGUAGE plpgsql
    AS $$
BEGIN

    IF UPPER(p_mode) NOT IN (
        'MISSING',
        'ALL',
        'SYMBOL'
    ) THEN
        RAISE EXCEPTION
            'Invalid refresh mode: %',
            p_mode;
    END IF;

    IF (
        UPPER(p_mode) = 'SYMBOL'
        AND NULLIF(TRIM(p_symbol), '') IS NULL
    ) THEN
        RAISE EXCEPTION
            'p_symbol is required when p_mode is SYMBOL';
    END IF;

    RETURN QUERY

    SELECT
        e.entity_id,
        i.instrument_id,
        i.symbol

    FROM entities e

    JOIN instruments i
      ON i.entity_id = e.entity_id

    WHERE
        e.active = TRUE

        AND
        (
            (
                UPPER(p_mode) = 'MISSING'
                AND
                (
                    UPPER(e.name) =
                    UPPER(i.symbol)

                    OR e.country_code IS NULL
                    OR i.currency_code IS NULL
                    OR i.data_source IS NULL
                )
            )

            OR UPPER(p_mode) = 'ALL'

            OR
            (
                UPPER(p_mode) = 'SYMBOL'
                AND UPPER(i.symbol) =
                    UPPER(TRIM(p_symbol))
            )
        )

    ORDER BY
        i.symbol,
        i.instrument_id;

END;
$$;


--
-- Name: sp_get_instruments(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_instruments(p_limit integer DEFAULT 1000) RETURNS TABLE(instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, instrument_type character varying, currency_code character, name character varying)
    LANGUAGE sql
    AS $$
SELECT
    i.instrument_id,
    i.entity_id,
    i.symbol,
    i.exchange,
    i.instrument_type::varchar,
    i.currency_code,

    e.name

FROM instruments i

JOIN entities e
ON e.entity_id =
   i.entity_id

ORDER BY
    i.symbol

LIMIT p_limit;
$$;


--
-- Name: sp_get_intelligence_candidates(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_intelligence_candidates() RETURNS TABLE(user_id uuid, entity_id uuid, instrument_id uuid, symbol character varying, name character varying, exchange character varying, currency_code character, instrument_type character varying, quantity numeric, market_value numeric, allocation_pct numeric, in_portfolio boolean, in_watchlist boolean)
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
                p.quantity * i.last_price,
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


--
-- Name: sp_get_market_cache(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_market_cache(p_cache_key character varying, p_max_age_hours integer) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_payload JSONB;
BEGIN

    SELECT payload
    INTO v_payload
    FROM market_search_cache
    WHERE cache_key =
          p_cache_key
      AND cached_at >
          NOW()
          - make_interval(
                hours =>
                p_max_age_hours
            );

    RETURN v_payload;

END;
$$;


--
-- Name: sp_get_portfolio_summary(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_portfolio_summary(p_portfolio_id uuid, p_user_id uuid) RETURNS TABLE(portfolio_currency character, reporting_currency character, portfolio_value_native numeric, portfolio_value_reporting numeric, positions_count bigint)
    LANGUAGE sql
    AS $$

WITH portfolio_data AS
(
    SELECT
        p.base_currency,

        u.reporting_currency,

        SUM(
            pos.quantity *
            i.last_price
        ) AS total_value,

        COUNT(*) AS positions_count

    FROM portfolios p

    JOIN users u
      ON u.user_id =
         p.user_id

    JOIN positions pos
      ON pos.portfolio_id =
         p.portfolio_id

    JOIN instruments i
      ON i.instrument_id =
         pos.instrument_id

    WHERE p.portfolio_id =
          p_portfolio_id

      AND p.user_id =
          p_user_id

    GROUP BY
        p.base_currency,
        u.reporting_currency
)

SELECT
    base_currency,

    reporting_currency,

    total_value,

    fx_convert(
        total_value,
        base_currency,
        reporting_currency
    ),

    positions_count

FROM portfolio_data;

$$;


--
-- Name: sp_get_portfolios(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_portfolios(p_user_id uuid) RETURNS TABLE(portfolio_id uuid, user_id uuid, name character varying, portfolio_type character varying, description text, base_currency character, created_at timestamp with time zone, updated_at timestamp with time zone)
    LANGUAGE sql
    AS $$
SELECT
    portfolio_id,
    user_id,
    name,
    portfolio_type::varchar,
    description,
    base_currency,
    created_at,
    updated_at
FROM portfolios
WHERE user_id = p_user_id
ORDER BY name;
$$;


--
-- Name: sp_get_positions(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_positions(p_portfolio_id uuid, p_user_id uuid) RETURNS TABLE(position_id uuid, portfolio_id uuid, quantity numeric, avg_price numeric, opened_at timestamp with time zone, notes text, instrument_id uuid, symbol character varying, exchange character varying, instrument_type character varying, currency_code character, name character varying, last_price numeric, market_value numeric, profit_loss numeric, profit_loss_pct numeric)
    LANGUAGE sql
    AS $$
SELECT

    p.position_id,
    p.portfolio_id,

    p.quantity,
    p.avg_price,
    p.opened_at,
    p.notes,

    i.instrument_id,
    i.symbol,
    i.exchange,
    i.instrument_type,
    i.currency_code,

    e.name,

	i.last_price,

	(p.quantity * i.last_price)
	  AS market_value,
	
	((i.last_price - p.avg_price)
	 * p.quantity)
	  AS profit_loss,
	
	CASE
	  WHEN p.avg_price > 0
	  THEN (
	    (i.last_price - p.avg_price)
	    / p.avg_price
	  ) * 100
	END
	  AS profit_loss_pct

FROM positions p

JOIN instruments i
ON i.instrument_id =
   p.instrument_id

LEFT JOIN entities e
ON e.entity_id =
   i.entity_id

WHERE p.portfolio_id =
      p_portfolio_id
AND p_portfolio_id IN (SELECT portfolio_id FROM portfolios WHERE user_id = p_user_id)

ORDER BY
    i.symbol;
$$;


--
-- Name: sp_get_recent_catalysts(uuid[], integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_recent_catalysts(p_entity_ids uuid[], p_days integer DEFAULT 30) RETURNS TABLE(entity_id uuid, catalyst_type character varying, catalyst_date timestamp with time zone, status character varying, importance numeric, title text, source character varying, raw_payload jsonb)
    LANGUAGE sql STABLE
    AS $$
SELECT
    c.entity_id,
    c.catalyst_type,
    c.catalyst_date,
    c.status,
    c.importance,
    c.title,
    c.source,
    c.raw_payload
FROM catalysts c
WHERE c.entity_id = ANY(p_entity_ids)
  AND c.catalyst_date >=
      now() - make_interval(days => p_days)
ORDER BY c.catalyst_date DESC;
$$;


--
-- Name: sp_get_symbol(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_symbol(p_symbol character varying, p_cache_hours integer DEFAULT 168) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_symbol VARCHAR;

    v_local JSONB;
    v_cache JSONB;
BEGIN

    v_symbol :=
        UPPER(
            TRIM(
                p_symbol
            )
        );

    ------------------------------------------------------------------
    -- LOCAL
    ------------------------------------------------------------------

    SELECT
        jsonb_build_object(
            'found', TRUE,
            'source', 'LOCAL',

            'payload',
            jsonb_build_object(
                'entity_id',
                e.entity_id,

                'instrument_id',
                i.instrument_id,

                'symbol',
                i.symbol,

                'name',
                e.name,

                'exchange',
                i.exchange,

                'type',
                i.instrument_type
            )
        )
    INTO v_local
    FROM instruments i
    JOIN entities e
      ON e.entity_id =
         i.entity_id
    WHERE UPPER(i.symbol) =
          v_symbol
    LIMIT 1;

    IF v_local IS NOT NULL THEN

        RETURN v_local;

    END IF;

    ------------------------------------------------------------------
    -- CACHE
    ------------------------------------------------------------------

    v_cache :=
        sp_get_market_cache(
            LOWER(v_symbol),
            p_cache_hours
        );

    IF v_cache IS NOT NULL
       AND jsonb_typeof(v_cache) = 'array'
    THEN

        RETURN jsonb_build_object(
            'found',
            TRUE,

            'source',
            'CACHE',

            'payload',
            (
                SELECT item
                FROM jsonb_array_elements(
                    v_cache
                ) item
                WHERE
                    UPPER(
                        item->>'symbol'
                    )
                    =
                    v_symbol
                LIMIT 1
            )
        );

    END IF;

    ------------------------------------------------------------------
    -- MISS
    ------------------------------------------------------------------

    RETURN jsonb_build_object(
        'found',
        FALSE,

        'source',
        'MISS',

        'payload',
        NULL
    );

END;
$$;


--
-- Name: sp_get_user_for_login(character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_for_login(p_username character varying) RETURNS TABLE(user_id uuid, username character varying, password_hash text, is_active boolean)
    LANGUAGE sql
    AS $$
SELECT
    user_id,
    username,
    password_hash,
    is_active
FROM users
WHERE username =
      p_username;
$$;


--
-- Name: sp_get_user_notification_source(uuid, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_notification_source(p_user_id uuid, p_source_type character varying) RETURNS TABLE(source_id uuid, source_type character varying, source_name character varying, external_id text, config jsonb)
    LANGUAGE sql STABLE
    AS $$
SELECT
    us.source_id,
    us.source_type,
    us.source_name,
    us.external_id,
    us.config
FROM user_sources us
WHERE us.user_id = p_user_id
  AND UPPER(us.source_type) =
      UPPER(p_source_type)
  AND COALESCE(
        (us.config->>'enabled')::boolean,
        true
      ) = true
ORDER BY us.created_at DESC
LIMIT 1;
$$;


--
-- Name: sp_get_watchlist_items(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_watchlist_items(p_watchlist_id uuid, p_user_id uuid) RETURNS TABLE(watchlist_item_id uuid, watchlist_id uuid, entity_id uuid, priority smallint, target_price numeric, notes text, name character varying, symbol character varying, exchange character varying, last_price numeric)
    LANGUAGE sql
    AS $$
SELECT
    wi.watchlist_item_id,
    wi.watchlist_id,
    wi.entity_id,

    wi.priority,
    wi.target_price,
    wi.notes,

    e.name,

    i.symbol,
    i.exchange,
	i.last_price

FROM watchlist_items wi

JOIN entities e
ON e.entity_id =
   wi.entity_id

LEFT JOIN instruments i
ON i.entity_id =
   e.entity_id

WHERE wi.watchlist_id =
      p_watchlist_id
AND p_watchlist_id IN (SELECT watchlist_id FROM watchlists WHERE user_id = p_user_id)

ORDER BY
    i.symbol NULLS LAST,
    e.name;
$$;


--
-- Name: sp_get_watchlists(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_watchlists(p_user_id uuid) RETURNS TABLE(watchlist_id uuid, user_id uuid, name character varying, description text, created_at timestamp with time zone, updated_at timestamp with time zone)
    LANGUAGE sql
    AS $$
SELECT
    watchlist_id,
    user_id,
    name,
    description,
    created_at,
    updated_at
FROM watchlists
WHERE user_id = p_user_id
ORDER BY name;
$$;


--
-- Name: sp_import_asset_profile(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_import_asset_profile(p_payload jsonb) RETURNS TABLE(entity_id uuid, instrument_id uuid, imported boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_symbol varchar(50);
    v_name varchar(255);

    v_exchange varchar(50);
    v_exchange_full_name varchar(255);

    v_currency char(3);
    v_entity_type entity_type;
    v_instrument_type varchar(50);

    v_country_code varchar(2);
    v_data_source varchar(50);
    v_last_price numeric(20,8);

    v_entity_id uuid;
    v_instrument_id uuid;
BEGIN

    --------------------------------------------------------
    -- Lettura e validazione symbol
    --------------------------------------------------------

    v_symbol :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'symbol'
                )
            ),
            ''
        );

    IF v_symbol IS NULL THEN
        RAISE EXCEPTION
            'Payload symbol is required';
    END IF;

    --------------------------------------------------------
    -- Lettura nome
    --------------------------------------------------------

    v_name :=
        NULLIF(
            TRIM(
                COALESCE(
                    p_payload->>'name',
                    p_payload->>'companyName'
                )
            ),
            ''
        );

    v_name :=
        COALESCE(
            v_name,
            v_symbol
        );

    --------------------------------------------------------
    -- Lettura exchange
    --------------------------------------------------------

    v_exchange :=
        NULLIF(
            TRIM(
                p_payload->>'exchange'
            ),
            ''
        );

    v_exchange_full_name :=
        NULLIF(
            TRIM(
                p_payload->>'exchangeFullName'
            ),
            ''
        );

    --------------------------------------------------------
    -- Lettura valuta dello strumento
    --------------------------------------------------------

    v_currency :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'currency'
                )
            ),
            ''
        );

    --------------------------------------------------------
    -- Tipo dello strumento
    --------------------------------------------------------

    v_instrument_type :=
        COALESCE(
            NULLIF(
                UPPER(
                    TRIM(
                        COALESCE(
                            p_payload->>'instrument_type',
                            p_payload->>'asset_sub_class',
                            p_payload->>'type'
                        )
                    )
                ),
                ''
            ),
            'STOCK'
        );

    --------------------------------------------------------
    -- Tipo dell'entity
    --------------------------------------------------------

    v_entity_type :=
        CASE

            WHEN UPPER(
                COALESCE(
                    p_payload->>'asset_class',
                    ''
                )
            ) IN (
                'CRYPTO',
                'CRYPTOCURRENCY'
            )
                THEN 'CRYPTO'::entity_type

            WHEN v_instrument_type = 'ETF'
                THEN 'ETF'::entity_type

            WHEN v_instrument_type IN (
                'FUND',
                'MUTUALFUND',
                'MUTUAL_FUND'
            )
                THEN 'FUND'::entity_type

            WHEN v_instrument_type = 'INDEX'
                THEN 'INDEX'::entity_type

            WHEN v_instrument_type = 'COMMODITY'
                THEN 'COMMODITY'::entity_type

            ELSE 'COMPANY'::entity_type

        END;

    --------------------------------------------------------
    -- Paese dell'entity
    --------------------------------------------------------

    v_country_code :=
        NULLIF(
            UPPER(
                TRIM(
                    COALESCE(
                        p_payload->>'country_code',
                        p_payload->>'country'
                    )
                )
            ),
            ''
        );

    --------------------------------------------------------
    -- Provider market data
    --------------------------------------------------------

    v_data_source :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'data_source'
                )
            ),
            ''
        );

    --------------------------------------------------------
    -- Ultimo prezzo
    --------------------------------------------------------

    v_last_price :=
        NULLIF(
            TRIM(
                p_payload->>'last_price'
            ),
            ''
        )::numeric;

    --------------------------------------------------------
    -- Upsert exchange
    --------------------------------------------------------

    IF v_exchange IS NOT NULL THEN

        PERFORM sp_upsert_exchange(
            v_exchange,
            v_exchange_full_name
        );

    END IF;

    --------------------------------------------------------
    -- Verifica se lo strumento esiste già
    --
    -- IS NOT DISTINCT FROM tratta correttamente anche
    -- il caso in cui entrambi gli exchange siano NULL.
    --------------------------------------------------------

    SELECT
        i.entity_id,
        i.instrument_id
    INTO
        v_entity_id,
        v_instrument_id
    FROM instruments i
    WHERE UPPER(i.symbol) = v_symbol
      AND i.exchange IS NOT DISTINCT FROM v_exchange
    LIMIT 1;

    --------------------------------------------------------
    -- Aggiornamento strumento già esistente
    --------------------------------------------------------

    IF v_instrument_id IS NOT NULL THEN

        UPDATE entities
        SET
            name = COALESCE(
                v_name,
                name
            ),

            entity_type = COALESCE(
                v_entity_type,
                entity_type
            ),

            country_code = COALESCE(
                v_country_code,
                country_code
            )

        WHERE entities.entity_id =
              v_entity_id;

        UPDATE instruments
        SET
            exchange = COALESCE(
                v_exchange,
                exchange
            ),

            instrument_type = COALESCE(
                v_instrument_type,
                instrument_type
            ),

            currency_code = COALESCE(
                v_currency,
                currency_code
            ),

            data_source = COALESCE(
                v_data_source,
                data_source
            ),

            last_price = COALESCE(
                v_last_price,
                last_price
            )

        WHERE instruments.instrument_id =
              v_instrument_id;

        RETURN QUERY
        SELECT
            v_entity_id,
            v_instrument_id,
            FALSE;

        RETURN;

    END IF;

    --------------------------------------------------------
    -- Creazione nuova entity
    --------------------------------------------------------

    INSERT INTO entities
    (
        name,
        entity_type,
        country_code
    )
    VALUES
    (
        v_name,
        v_entity_type,
        v_country_code
    )
    RETURNING entities.entity_id
    INTO v_entity_id;

    --------------------------------------------------------
    -- Creazione nuovo instrument
    --------------------------------------------------------

    INSERT INTO instruments
    (
        entity_id,
        symbol,
        exchange,
        instrument_type,
        currency_code,
        data_source,
        last_price
    )
    VALUES
    (
        v_entity_id,
        v_symbol,
        v_exchange,
        v_instrument_type,
        v_currency,
        v_data_source,
        v_last_price
    )
    RETURNING instruments.instrument_id
    INTO v_instrument_id;

    --------------------------------------------------------
    -- Risultato
    --------------------------------------------------------

    RETURN QUERY
    SELECT
        v_entity_id,
        v_instrument_id,
        TRUE;

END;
$$;


--
-- Name: sp_mark_notification_failed(uuid, text, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_mark_notification_failed(p_notification_id uuid, p_error text, p_max_retries integer, p_retry_base_seconds integer) RETURNS character varying
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_attempt_count integer;
    v_status varchar;
BEGIN
    SELECT attempt_count
    INTO v_attempt_count
    FROM notifications
    WHERE notification_id = p_notification_id
      AND status = 'PROCESSING'
    FOR UPDATE;

    IF v_attempt_count IS NULL THEN
        RAISE EXCEPTION
            'PROCESSING notification not found: %',
            p_notification_id;
    END IF;

    IF v_attempt_count <= p_max_retries THEN
        v_status := 'RETRY';
    ELSE
        v_status := 'FAILED';
    END IF;

    UPDATE notifications
    SET
        status = v_status,
        last_error = LEFT(p_error, 4000),
        processing_started_at = NULL,
        next_attempt_at = CASE
            WHEN v_status = 'RETRY'
            THEN now() + make_interval(
                secs => p_retry_base_seconds
                    * CAST(power(2, GREATEST(v_attempt_count - 1, 0)) AS integer)
            )
            ELSE NULL
        END
    WHERE notification_id = p_notification_id;

    RETURN v_status;
END;
$$;


--
-- Name: sp_mark_notification_sent(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_mark_notification_sent(p_notification_id uuid, p_provider_message_id text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE notifications
    SET
        status = 'SENT',
        sent_at = now(),
        delivered_message_id = p_provider_message_id,
        processing_started_at = NULL,
        next_attempt_at = NULL,
        last_error = NULL
    WHERE notification_id = p_notification_id
      AND status = 'PROCESSING';

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'PROCESSING notification not found: %',
            p_notification_id;
    END IF;
END;
$$;


--
-- Name: sp_notification_exists(uuid, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_notification_exists(p_user_id uuid, p_notification_key character varying) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
SELECT EXISTS(SELECT 1 FROM notifications n
              WHERE n.user_id=p_user_id AND n.notification_key=p_notification_key AND n.status='SENT');
$$;


--
-- Name: sp_purge_notifications(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_purge_notifications(p_days integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_deleted integer;
BEGIN
    IF p_days < 1 THEN RAISE EXCEPTION 'p_days must be >= 1'; END IF;
    DELETE FROM notifications
    WHERE status='SENT'
      AND sent_at < now() - make_interval(days => p_days)
      AND branch IN ('DAILY_MOVER','UPCOMING_CATALYST','BREAKING_CATALYST');
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;


--
-- Name: sp_refresh_entity_from_profile(uuid, uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_refresh_entity_from_profile(p_entity_id uuid, p_instrument_id uuid, p_payload jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_name varchar(255);
    v_country_code varchar(2);

    v_exchange varchar(50);
    v_exchange_full_name varchar(255);
    v_currency varchar(3);
    v_data_source varchar(50);
    v_last_price numeric(20,8);
BEGIN

    --------------------------------------------------------
    -- Validazione relazione entity/instrument
    --------------------------------------------------------

    IF NOT EXISTS
    (
        SELECT 1
        FROM instruments i
        WHERE i.instrument_id = p_instrument_id
          AND i.entity_id = p_entity_id
    ) THEN
        RAISE EXCEPTION
            'Instrument % does not belong to entity %',
            p_instrument_id,
            p_entity_id;
    END IF;

    --------------------------------------------------------
    -- Lettura attributi entity
    --------------------------------------------------------

    v_name :=
        NULLIF(
            TRIM(
                COALESCE(
                    p_payload->>'name',
                    p_payload->>'companyName'
                )
            ),
            ''
        );

    v_country_code :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'country_code'
                )
            ),
            ''
        );

    --------------------------------------------------------
    -- Lettura attributi instrument
    --------------------------------------------------------

    v_exchange :=
        NULLIF(
            TRIM(
                p_payload->>'exchange'
            ),
            ''
        );

    v_exchange_full_name :=
        NULLIF(
            TRIM(
                p_payload->>'exchangeFullName'
            ),
            ''
        );

    v_currency :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'currency'
                )
            ),
            ''
        );

    v_data_source :=
        NULLIF(
            UPPER(
                TRIM(
                    p_payload->>'data_source'
                )
            ),
            ''
        );

    v_last_price :=
        NULLIF(
            TRIM(
                p_payload->>'last_price'
            ),
            ''
        )::numeric;

    --------------------------------------------------------
    -- Exchange
    --------------------------------------------------------

    IF v_exchange IS NOT NULL THEN

        PERFORM sp_upsert_exchange(
            v_exchange,
            v_exchange_full_name
        );

    END IF;

    --------------------------------------------------------
    -- Entity
    --------------------------------------------------------

    UPDATE entities
    SET
        name = COALESCE(
            v_name,
            name
        ),

        country_code = COALESCE(
            v_country_code,
            country_code
        )

    WHERE entity_id = p_entity_id;

    --------------------------------------------------------
    -- Singolo instrument
    --------------------------------------------------------

    UPDATE instruments
    SET
        exchange = COALESCE(
            v_exchange,
            exchange
        ),

        currency_code = COALESCE(
            v_currency,
            currency_code
        ),

        data_source = COALESCE(
            v_data_source,
            data_source
        ),

        last_price = COALESCE(
            v_last_price,
            last_price
        )

    WHERE instrument_id = p_instrument_id;

END;
$$;


--
-- Name: sp_register_scheduler_job(character varying, character varying, character varying, character varying, character varying, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_register_scheduler_job(p_job_key character varying, p_job_name character varying, p_python_module character varying, p_trigger_type character varying, p_cron_expression character varying, p_interval_minutes integer, p_enabled_by_default boolean) RETURNS TABLE(job_id uuid, job_key character varying, action character varying)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job_id uuid;
    v_action varchar;
BEGIN
    IF NULLIF(
        TRIM(p_job_key),
        ''
    ) IS NULL THEN
        RAISE EXCEPTION
            'job_key cannot be empty';
    END IF;

    IF NULLIF(
        TRIM(p_job_name),
        ''
    ) IS NULL THEN
        RAISE EXCEPTION
            'job_name cannot be empty';
    END IF;

    IF NULLIF(
        TRIM(p_python_module),
        ''
    ) IS NULL THEN
        RAISE EXCEPTION
            'python_module cannot be empty';
    END IF;

    IF LOWER(p_trigger_type)
        NOT IN ('cron', 'interval')
    THEN
        RAISE EXCEPTION
            'Unsupported trigger_type: %',
            p_trigger_type;
    END IF;

    IF LOWER(p_trigger_type) = 'cron'
       AND NULLIF(
           TRIM(p_cron_expression),
           ''
       ) IS NULL
    THEN
        RAISE EXCEPTION
            'cron_expression is required';
    END IF;

    IF LOWER(p_trigger_type) = 'interval'
       AND COALESCE(
           p_interval_minutes,
           0
       ) <= 0
    THEN
        RAISE EXCEPTION
            'interval_minutes must be '
            'greater than zero';
    END IF;

    SELECT sj.job_id
    INTO v_job_id
    FROM scheduler_jobs sj
    WHERE sj.job_key = p_job_key
    FOR UPDATE;

    IF v_job_id IS NULL THEN
        INSERT INTO scheduler_jobs
        (
            job_key,
            job_name,
            python_module,
            trigger_type,
            cron_expression,
            interval_minutes,
            enabled
        )
        VALUES
        (
            p_job_key,
            p_job_name,
            p_python_module,
            LOWER(p_trigger_type),
            CASE
                WHEN LOWER(
                    p_trigger_type
                ) = 'cron'
                    THEN p_cron_expression
                ELSE NULL
            END,
            CASE
                WHEN LOWER(
                    p_trigger_type
                ) = 'interval'
                    THEN p_interval_minutes
                ELSE NULL
            END,
            p_enabled_by_default
        )
        RETURNING scheduler_jobs.job_id
        INTO v_job_id;

        v_action := 'INSERTED';

    ELSE
        UPDATE scheduler_jobs sj
        SET
            job_name = p_job_name,
            python_module =
                p_python_module,
            trigger_type =
                LOWER(p_trigger_type),
            cron_expression =
                CASE
                    WHEN LOWER(
                        p_trigger_type
                    ) = 'cron'
                        THEN p_cron_expression
                    ELSE NULL
                END,
            interval_minutes =
                CASE
                    WHEN LOWER(
                        p_trigger_type
                    ) = 'interval'
                        THEN
                            p_interval_minutes
                    ELSE NULL
                END
        WHERE sj.job_id = v_job_id
          AND
          (
              sj.job_name
                  IS DISTINCT FROM
                  p_job_name
              OR sj.python_module
                  IS DISTINCT FROM
                  p_python_module
              OR sj.trigger_type
                  IS DISTINCT FROM
                  LOWER(p_trigger_type)
              OR sj.cron_expression
                  IS DISTINCT FROM
                  CASE
                      WHEN LOWER(
                          p_trigger_type
                      ) = 'cron'
                          THEN
                              p_cron_expression
                      ELSE NULL
                  END
              OR sj.interval_minutes
                  IS DISTINCT FROM
                  CASE
                      WHEN LOWER(
                          p_trigger_type
                      ) = 'interval'
                          THEN
                              p_interval_minutes
                      ELSE NULL
                  END
          );

        IF FOUND THEN
            v_action := 'UPDATED';
        ELSE
            v_action := 'UNCHANGED';
        END IF;
    END IF;

    RETURN QUERY
    SELECT
        v_job_id,
        p_job_key,
        v_action;
END;
$$;


--
-- Name: sp_release_stale_notifications(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_release_stale_notifications(p_timeout_seconds integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_count integer;
BEGIN
    UPDATE notifications
    SET
        status = 'RETRY',
        processing_started_at = NULL,
        next_attempt_at = now(),
        last_error = COALESCE(
            last_error,
            'Recovered from stale PROCESSING state'
        )
    WHERE status = 'PROCESSING'
      AND processing_started_at
          < now() - make_interval(secs => p_timeout_seconds);

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: sp_search_assets(character varying, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_search_assets(p_query character varying, p_limit integer DEFAULT 20, p_cache_hours integer DEFAULT 24) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_query VARCHAR;

    v_local_results JSONB;
    v_cache_results JSONB;
BEGIN

    v_query :=
        LOWER(
            TRIM(
                p_query
            )
        );

    ----------------------------------------------------
    -- L1
    -- Local instruments/entities
    ----------------------------------------------------

    v_local_results :=
        sp_search_local_assets(
            v_query,
            p_limit
        );

    IF v_local_results IS NOT NULL
       AND jsonb_array_length(
           v_local_results
       ) > 0
    THEN

        RETURN jsonb_build_object(
            'source',
            'LOCAL',

            'results',
            v_local_results
        );

    END IF;

    ----------------------------------------------------
    -- L2
    -- Search cache
    ----------------------------------------------------

    v_cache_results :=
        sp_get_market_cache(
            v_query,
            p_cache_hours
        );

    IF v_cache_results IS NOT NULL
       AND jsonb_array_length(
           v_cache_results
       ) > 0
    THEN

        RETURN jsonb_build_object(
            'source',
            'CACHE',

            'results',
            v_cache_results
        );

    END IF;

    ----------------------------------------------------
    -- MISS
    ----------------------------------------------------

    RETURN jsonb_build_object(
        'source',
        'MISS',

        'results',
        '[]'::jsonb
    );

END;
$$;


--
-- Name: sp_search_local_assets(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_search_local_assets(p_query character varying, p_limit integer DEFAULT 20) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_result JSONB;
BEGIN

    SELECT
        COALESCE(
            jsonb_agg(row_data),
            '[]'::jsonb
        )
    INTO v_result
    FROM
    (
        SELECT
            jsonb_build_object(
                'symbol',
                i.symbol,

                'name',
                e.name,

                'exchange',
                i.exchange,

                'type',
                i.instrument_type
            ) AS row_data

        FROM instruments i

        JOIN entities e
          ON e.entity_id =
             i.entity_id

        WHERE
            LOWER(i.symbol)
                LIKE LOWER(
                    p_query || '%'
                )
            OR
            LOWER(e.name)
                LIKE LOWER(
                    p_query || '%'
                )

        ORDER BY

            CASE

                WHEN LOWER(i.symbol)
                     =
                     LOWER(p_query)
                THEN 1

                WHEN LOWER(i.symbol)
                     LIKE
                     LOWER(
                        p_query || '%'
                     )
                THEN 2

                ELSE 3

            END,

            i.symbol

        LIMIT p_limit

    ) q;

    RETURN v_result;

END;
$$;


--
-- Name: sp_start_scheduler_job(uuid, uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_start_scheduler_job(p_job_run_id uuid, p_job_id uuid, p_started_at timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO scheduler_job_runs(job_run_id, job_id, started_at, status)
    VALUES (p_job_run_id, p_job_id, p_started_at, 'RUNNING');
END;
$$;


--
-- Name: sp_update_entity_profile(uuid, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_update_entity_profile(p_entity_id uuid, p_name character varying, p_country_code character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN

    UPDATE entities
    SET
        name = COALESCE(
            NULLIF(
                TRIM(p_name),
                ''
            ),
            name
        ),

        country_code = COALESCE(
            NULLIF(
                UPPER(
                    TRIM(p_country_code)
                ),
                ''
            ),
            country_code
        )

    WHERE entity_id = p_entity_id;

END;
$$;


--
-- Name: sp_upsert_catalyst(text, text, timestamp without time zone, text, text, text, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_catalyst(p_symbol text, p_entity_subtype text, p_entity_date timestamp without time zone, p_entity_status text, p_importance text, p_title text, p_source text, p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE

    v_entity_id UUID;
    v_instrument_id UUID;

    v_catalyst_type VARCHAR(50);

    v_importance NUMERIC;

BEGIN

    ------------------------------------------------------------------
    -- Map catalyst type
    ------------------------------------------------------------------

    v_catalyst_type :=
        CASE p_entity_subtype

            WHEN 'QUARTERLY_EARNINGS'
                THEN 'EARNINGS'

            WHEN 'CASH_DIVIDEND'
                THEN 'DIVIDEND'

            WHEN 'STOCK_SPLIT'
                THEN 'SPLIT'

            ELSE 'OTHER'

        END;

    ------------------------------------------------------------------
    -- Map importance
    ------------------------------------------------------------------

    v_importance :=
        CASE UPPER(COALESCE(p_importance,'MEDIUM'))

            WHEN 'HIGH' THEN 90
            WHEN 'MEDIUM' THEN 50
            WHEN 'LOW' THEN 10

            ELSE 50

        END;

    ------------------------------------------------------------------
    -- Resolve existing instrument
    ------------------------------------------------------------------

    SELECT
        i.instrument_id,
        e.entity_id
    INTO
        v_instrument_id,
        v_entity_id
    FROM instruments i
    JOIN entities e
        ON e.entity_id = i.entity_id
    WHERE i.symbol = p_symbol
    LIMIT 1;


    ------------------------------------------------------------------
    -- Create entity if missing
    ------------------------------------------------------------------

    IF v_entity_id IS NULL THEN

        INSERT INTO entities
        (
            name,
            entity_type
        )
        VALUES
        (
            p_symbol,
            'COMPANY'
        )
        RETURNING entity_id
        INTO v_entity_id;

    END IF;

    ------------------------------------------------------------------
    -- Create instrument if missing
    ------------------------------------------------------------------

    IF v_instrument_id IS NULL THEN

        INSERT INTO instruments
        (
            entity_id,
            symbol,
            instrument_type
        )
        VALUES
        (
            v_entity_id,
            p_symbol,
            'STOCK'
        )
        ON CONFLICT DO NOTHING;

    END IF;

    ------------------------------------------------------------------
    -- Upsert catalyst
    ------------------------------------------------------------------

    INSERT INTO catalysts
    (
        entity_id,
        catalyst_type,
        catalyst_date,
        status,
        importance,
        title,
        source,
        raw_payload
    )
    VALUES
    (
        v_entity_id,
        v_catalyst_type,
        p_entity_date,
        p_entity_status,
        v_importance,
        p_title,
        p_source,
        p_payload
    )
    ON CONFLICT
    (
        entity_id,
        catalyst_type,
        catalyst_date
    )
    DO UPDATE
    SET
        status      = EXCLUDED.status,
        importance  = EXCLUDED.importance,
        title       = EXCLUDED.title,
        source      = EXCLUDED.source,
        raw_payload = EXCLUDED.raw_payload;

    RETURN v_entity_id;

END;
$$;


--
-- Name: sp_upsert_exchange(character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_exchange(p_exchange character varying, p_exchange_full_name character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN

    IF p_exchange IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO exchanges
    (
        exchange,
        exchangeFullName
    )
    VALUES
    (
        p_exchange,
        COALESCE(
            p_exchange_full_name,
            p_exchange
        )
    )
    ON CONFLICT (exchange)
    DO UPDATE
    SET
        exchangeFullName =
            COALESCE(
                EXCLUDED.exchangeFullName,
                exchanges.exchangeFullName
            );

END;
$$;


--
-- Name: sp_upsert_exchange_rate(character, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_exchange_rate(p_currency_code character, p_rate_to_usd numeric) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_currency_code char(3);
BEGIN
    v_currency_code :=
        UPPER(TRIM(p_currency_code));

    IF v_currency_code IS NULL
       OR LENGTH(v_currency_code) <> 3
    THEN
        RAISE EXCEPTION
            'Invalid currency code: %',
            p_currency_code;
    END IF;

    IF p_rate_to_usd IS NULL
       OR p_rate_to_usd <= 0
    THEN
        RAISE EXCEPTION
            'Invalid exchange rate for %: %',
            v_currency_code,
            p_rate_to_usd;
    END IF;

    INSERT INTO exchange_rates
    (
        currency_code,
        rate_to_usd
    )
    VALUES
    (
        v_currency_code,
        p_rate_to_usd
    )
    ON CONFLICT (currency_code)
    DO UPDATE
    SET
        rate_to_usd =
            EXCLUDED.rate_to_usd;
END;
$$;


--
-- Name: sp_upsert_filing(text, text, text, date, timestamp without time zone, text, text, jsonb, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_filing(p_symbol text, p_filing_type text, p_cik text, p_filing_date date, p_accepted_at timestamp without time zone, p_title text, p_source text, p_raw_payload jsonb, p_filing_subtype text DEFAULT NULL::text, p_accession_number text DEFAULT NULL::text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_entity_id UUID;
    v_instrument_id UUID;
BEGIN

    ------------------------------------------------------------------
    -- Resolve instrument
    ------------------------------------------------------------------

    SELECT
        i.instrument_id,
        e.entity_id
    INTO
        v_instrument_id,
        v_entity_id
    FROM instruments i
    JOIN entities e
        ON e.entity_id = i.entity_id
    WHERE i.symbol = p_symbol
    LIMIT 1;

    ------------------------------------------------------------------
    -- Create entity if missing
    ------------------------------------------------------------------

    IF v_entity_id IS NULL THEN

        INSERT INTO entities
        (
            name,
            entity_type
        )
        VALUES
        (
            p_symbol,
            'COMPANY'
        )
        RETURNING entity_id
        INTO v_entity_id;

    END IF;

    ------------------------------------------------------------------
    -- Create instrument if missing
    ------------------------------------------------------------------

    IF v_instrument_id IS NULL THEN

        INSERT INTO instruments
        (
            entity_id,
            symbol,
            instrument_type
        )
        VALUES
        (
            v_entity_id,
            p_symbol,
            'STOCK'
        )
        ON CONFLICT DO NOTHING;

    END IF;

    ------------------------------------------------------------------
    -- Upsert filing
    ------------------------------------------------------------------

    INSERT INTO filings
    (
        entity_id,
        filing_type,
        filing_subtype,
        filing_date,
        accepted_at,
        cik,
        accession_number,
        title,
        source,
        raw_payload
    )
    VALUES
    (
        v_entity_id,
        p_filing_type,
        p_filing_subtype,
        p_filing_date,
        p_accepted_at,
        p_cik,
        p_accession_number,
        p_title,
        p_source,
        p_raw_payload
    )
    ON CONFLICT
    (
        cik,
        accepted_at,
        filing_type
    )
    DO UPDATE
    SET
        filing_subtype   = EXCLUDED.filing_subtype,
        filing_date      = EXCLUDED.filing_date,
        accession_number = EXCLUDED.accession_number,
        title            = EXCLUDED.title,
        source           = EXCLUDED.source,
        raw_payload      = EXCLUDED.raw_payload;

    RETURN v_entity_id;

END;
$$;


--
-- Name: sp_upsert_market_cache(character varying, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_market_cache(p_cache_key character varying, p_payload jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN

    INSERT INTO market_search_cache
    (
        cache_key,
        payload,
        cached_at
    )
    VALUES
    (
        p_cache_key,
        p_payload,
        NOW()
    )
    ON CONFLICT (cache_key)
    DO UPDATE
    SET
        payload =
            EXCLUDED.payload,

        cached_at =
            NOW();

END;
$$;


--
-- Name: sp_upsert_notification(uuid, character varying, character varying, uuid, character varying, character varying, character varying, text, text, jsonb, character varying, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_notification(p_user_id uuid, p_notification_key character varying, p_branch character varying, p_entity_id uuid, p_symbol character varying, p_event_key character varying, p_severity character varying, p_title text, p_message text, p_context jsonb, p_status character varying, p_sent_at timestamp with time zone) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE v_notification_id uuid;
BEGIN
    INSERT INTO notifications(user_id, entity_id, notification_type, notification_key,
        branch, symbol, event_key, severity, status, title, message, context, observed_at, sent_at)
    VALUES(p_user_id, p_entity_id, p_branch, p_notification_key, p_branch, p_symbol,
        p_event_key, p_severity, UPPER(p_status), p_title, p_message,
        COALESCE(p_context, '{}'::jsonb), now(), p_sent_at)
    ON CONFLICT (user_id, notification_key) WHERE notification_key IS NOT NULL
    DO UPDATE SET status=EXCLUDED.status, message=EXCLUDED.message,
                  context=EXCLUDED.context, sent_at=EXCLUDED.sent_at
    RETURNING notification_id INTO v_notification_id;
    RETURN v_notification_id;
END;
$$;


--
-- Name: sp_upsert_portfolio(uuid, uuid, character varying, public.portfolio_type, text, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_portfolio(p_portfolio_id uuid, p_user_id uuid, p_name character varying, p_portfolio_type public.portfolio_type, p_description text, p_base_currency character) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_portfolio_id UUID;
BEGIN

    IF p_user_id IS NOT NULL AND p_user_id IN (SELECT user_id FROM users WHERE user_id = p_user_id) THEN
		IF p_portfolio_id IS NULL THEN
	
	        INSERT INTO portfolios
	        (
	            user_id,
	            name,
	            portfolio_type,
	            description,
	            base_currency
	        )
	        VALUES
	        (
	            p_user_id,
	            p_name,
	            p_portfolio_type,
	            p_description,
	            p_base_currency
	        )
	        RETURNING portfolio_id
	        INTO v_portfolio_id;
	
	    ELSE
	
	        UPDATE portfolios
	        SET
	            name = p_name,
	            portfolio_type =
	                p_portfolio_type,
	            description =
	                p_description,
	            base_currency =
	                p_base_currency
	        WHERE portfolio_id =
	              p_portfolio_id
	          AND user_id =
	              p_user_id;
	
	        v_portfolio_id :=
	            p_portfolio_id;
	
	    END IF;
	ELSE
		RAISE EXCEPTION 'User not found';
	END IF;

    RETURN v_portfolio_id;

END;
$$;


--
-- Name: sp_upsert_position(uuid, uuid, uuid, uuid, numeric, numeric, timestamp with time zone, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_position(p_position_id uuid, p_portfolio_id uuid, p_user_id uuid, p_instrument_id uuid, p_quantity numeric, p_avg_price numeric, p_opened_at timestamp with time zone, p_notes text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_position_id UUID;
BEGIN

	IF p_portfolio_id IS NOT NULL AND p_portfolio_id IN (SELECT portfolio_id FROM portfolios WHERE user_id = p_user_id) THEN
	    IF p_position_id IS NULL THEN
	
	        INSERT INTO positions
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
	
	        UPDATE positions
	        SET
	            instrument_id =
	                p_instrument_id,
	            quantity =
	                p_quantity,
	            avg_price =
	                p_avg_price,
	            opened_at =
	                p_opened_at,
	            notes =
	                p_notes
	        WHERE position_id =
	              p_position_id;
	
	        v_position_id :=
	            p_position_id;
	
	    END IF;
	ELSE
		RAISE EXCEPTION 'Portfolio not found';
	END IF;

    RETURN v_position_id;

END;
$$;


--
-- Name: sp_upsert_watchlist(uuid, uuid, character varying, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_watchlist(p_watchlist_id uuid, p_user_id uuid, p_name character varying, p_description text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_watchlist_id UUID;
BEGIN

    IF p_user_id IS NOT NULL AND p_user_id IN (SELECT user_id FROM users WHERE user_id = p_user_id) THEN
		IF p_watchlist_id IS NULL THEN
	
	        INSERT INTO watchlists
	        (
	            user_id,
	            name,
	            description
	        )
	        VALUES
	        (
	            p_user_id,
	            p_name,
	            p_description
	        )
	        RETURNING watchlist_id
	        INTO v_watchlist_id;
	
	    ELSE
	
	        UPDATE watchlists
	        SET
	            name = p_name,
	            description = p_description
	        WHERE watchlist_id = p_watchlist_id
	          AND user_id = p_user_id;
	
	        v_watchlist_id :=
	            p_watchlist_id;
	
	    END IF;
	ELSE
		RAISE EXCEPTION 'User not found';		
	END IF;

    RETURN v_watchlist_id;

END;
$$;


--
-- Name: sp_upsert_watchlist_item(uuid, uuid, uuid, uuid, smallint, numeric, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_watchlist_item(p_watchlist_item_id uuid, p_watchlist_id uuid, p_user_id uuid, p_entity_id uuid, p_priority smallint, p_target_price numeric, p_notes text) RETURNS uuid
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
	              p_watchlist_item_id;
	
	        v_watchlist_item_id :=
	            p_watchlist_item_id;
	
	    END IF;
	ELSE
		RAISE EXCEPTION 'Watchlist not found';	
	END IF;

    RETURN v_watchlist_item_id;

END;
$$;


--
-- Name: trg_set_created_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_set_created_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.created_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: trg_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: api_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_sources (
    api_source_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100),
    base_url text
);


--
-- Name: cache_portfolio_exposure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cache_portfolio_exposure (
    cache_id uuid DEFAULT gen_random_uuid() CONSTRAINT portfolio_exposure_cache_cache_id_not_null NOT NULL,
    portfolio_id uuid,
    entity_id uuid,
    exposure_pct numeric(10,4),
    calculated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cache_portfolio_sector_exposure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cache_portfolio_sector_exposure (
    id uuid DEFAULT gen_random_uuid() CONSTRAINT portfolio_sector_exposure_id_not_null NOT NULL,
    portfolio_id uuid,
    sector_id uuid,
    exposure_pct numeric(10,4),
    calculated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cache_portfolio_theme_exposure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cache_portfolio_theme_exposure (
    id uuid DEFAULT gen_random_uuid() CONSTRAINT portfolio_theme_exposure_id_not_null NOT NULL,
    portfolio_id uuid,
    tag_id uuid,
    exposure_pct numeric(10,4),
    calculated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalyst_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalyst_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    catalyst_id uuid,
    previous_status character varying(30),
    new_status character varying(30)
);


--
-- Name: catalysts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalysts (
    catalyst_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    catalyst_type character varying(50),
    catalyst_date timestamp with time zone,
    status character varying(30),
    importance numeric,
    title text,
    source character varying(50),
    raw_payload jsonb
);


--
-- Name: entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entities (
    entity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    entity_type public.entity_type NOT NULL,
    country_code character(2),
    description text,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: entity_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_aliases (
    alias_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    alias_name character varying(255)
);


--
-- Name: entity_impact_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_impact_history (
    id uuid DEFAULT gen_random_uuid() CONSTRAINT impact_history_id_not_null NOT NULL,
    portfolio_id uuid,
    entity_id uuid,
    impact_score numeric,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: entity_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_metrics (
    metric_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    metric_name character varying(100),
    metric_value numeric,
    metric_date date
);


--
-- Name: entity_relationships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_relationships (
    relationship_id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_entity_id uuid,
    target_entity_id uuid,
    relation_type character varying(50),
    strength numeric(5,2)
);


--
-- Name: entity_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_scores (
    entity_score_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    component_id uuid,
    score numeric,
    confidence numeric,
    calculated_at timestamp with time zone
);


--
-- Name: entity_sectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_sectors (
    entity_id uuid NOT NULL,
    sector_id uuid NOT NULL,
    weight numeric(5,2)
);


--
-- Name: entity_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_tags (
    entity_id uuid NOT NULL,
    tag_id uuid NOT NULL
);


--
-- Name: etf_constituents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.etf_constituents (
    constituent_id uuid DEFAULT gen_random_uuid() NOT NULL,
    etf_instrument_id uuid,
    entity_id uuid,
    weight numeric(10,6),
    effective_date date
);


--
-- Name: exchange_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_rates (
    currency_code character(3) NOT NULL,
    rate_to_usd numeric(18,8) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: exchanges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchanges (
    exchange character varying(20) NOT NULL,
    exchangefullname character varying(255) NOT NULL,
    country_code character varying(10),
    timezone_name character varying(64),
    market_open_time time without time zone,
    market_close_time time without time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: external_systems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.external_systems (
    system_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100),
    system_type character varying(50)
);


--
-- Name: filings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filings (
    filing_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid NOT NULL,
    filing_type character varying(20) NOT NULL,
    filing_date date,
    accepted_at timestamp with time zone,
    cik character varying(20),
    accession_number character varying(50),
    title text,
    source character varying(50),
    raw_payload jsonb,
    created_at timestamp with time zone DEFAULT now(),
    filing_subtype character varying(50)
);


--
-- Name: insider_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insider_transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    insider_name character varying(255),
    transaction_type character varying(20),
    shares numeric,
    price numeric,
    transaction_date date
);


--
-- Name: instrument_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instrument_aliases (
    alias_id uuid DEFAULT gen_random_uuid() NOT NULL,
    instrument_id uuid,
    alias_symbol character varying(100)
);


--
-- Name: instrument_entity_exposure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instrument_entity_exposure (
    exposure_id uuid DEFAULT gen_random_uuid() CONSTRAINT instrument_exposure_exposure_id_not_null NOT NULL,
    instrument_id uuid,
    entity_id uuid,
    weight numeric(10,6),
    effective_date date
);


--
-- Name: instrument_relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instrument_relations (
    relation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_instrument_id uuid,
    child_instrument_id uuid,
    relation_type character varying(50),
    weight numeric(10,4)
);


--
-- Name: instruments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instruments (
    instrument_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid NOT NULL,
    symbol character varying(50),
    exchange character varying(50),
    instrument_type character varying(50),
    currency_code character(3),
    data_source character varying(50),
    last_price numeric(20,8),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: market_search_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_search_cache (
    cache_key character varying(255) NOT NULL,
    payload jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() CONSTRAINT market_search_cache_created_at_not_null NOT NULL
);


--
-- Name: news; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news (
    news_id uuid DEFAULT gen_random_uuid() NOT NULL,
    cluster_id uuid,
    source_id uuid,
    title text,
    summary text,
    url text,
    content_hash character varying(64),
    published_at timestamp with time zone,
    raw_json jsonb
);


--
-- Name: news_clusters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_clusters (
    cluster_id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: news_entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_entities (
    news_entity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    news_id uuid,
    entity_id uuid,
    relevance numeric,
    sentiment numeric,
    impact numeric
);


--
-- Name: news_impacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_impacts (
    impact_id uuid DEFAULT gen_random_uuid() NOT NULL,
    news_id uuid,
    entity_id uuid,
    direct_impact numeric,
    propagated_impact numeric,
    confidence numeric
);


--
-- Name: news_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_sources (
    source_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100)
);


--
-- Name: notification_hashes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_hashes (
    hash character varying(64) NOT NULL,
    entity_id uuid
);


--
-- Name: notification_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_rules (
    rule_id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_name character varying(100),
    event_type character varying(50),
    threshold numeric
);


--
-- Name: notification_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_subscriptions (
    subscription_id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid,
    event_type character varying(50),
    min_confidence numeric,
    min_impact numeric,
    user_id uuid,
    source_id uuid,
    enabled boolean DEFAULT true NOT NULL,
    severity_min character varying(20),
    config jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: notification_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_templates (
    template_id uuid DEFAULT gen_random_uuid() NOT NULL,
    template_name character varying(100),
    subject_template text,
    body_template text
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    notification_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    notification_type character varying(50),
    title text,
    message text,
    sent_at timestamp with time zone,
    user_id uuid,
    notification_key character varying(255),
    branch character varying(50),
    symbol character varying(50),
    event_key character varying(255),
    severity character varying(20),
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    observed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_id uuid,
    channel character varying(50),
    attempt_count integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    last_error text,
    delivered_message_id text
);


--
-- Name: portfolio_holdings_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_holdings_snapshots (
    holding_snapshot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    snapshot_date date NOT NULL,
    quantity numeric(20,8),
    market_value numeric(20,2),
    weight numeric(10,4),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: portfolio_impacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_impacts (
    impact_id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid,
    entity_id uuid,
    impact_score numeric,
    calculated_at timestamp with time zone
);


--
-- Name: portfolio_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_snapshots (
    snapshot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid NOT NULL,
    snapshot_date date NOT NULL,
    market_value numeric(20,2),
    cost_basis numeric(20,2),
    unrealized_pnl numeric(20,2),
    unrealized_pnl_pct numeric(10,4),
    cash_value numeric(20,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: portfolios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolios (
    portfolio_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    portfolio_type public.portfolio_type NOT NULL,
    description text,
    base_currency character(3) DEFAULT 'CHF'::bpchar,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.positions (
    position_id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    quantity numeric(20,8) NOT NULL,
    avg_price numeric(20,8),
    opened_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text
);


--
-- Name: price_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_history (
    price_id uuid DEFAULT gen_random_uuid() NOT NULL,
    instrument_id uuid,
    ts timestamp with time zone,
    open numeric,
    high numeric,
    low numeric,
    close numeric,
    volume numeric
);


--
-- Name: scheduler_job_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduler_job_runs (
    job_run_id uuid NOT NULL,
    job_id uuid NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    status character varying(20) NOT NULL,
    error_message text,
    result jsonb
);


--
-- Name: scheduler_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduler_jobs (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_name character varying(100) NOT NULL,
    python_module character varying(255) NOT NULL,
    trigger_type character varying(20) NOT NULL,
    cron_expression character varying(100),
    interval_minutes integer,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    last_run_at timestamp with time zone,
    next_run_at timestamp with time zone,
    job_key character varying(100) NOT NULL
);


--
-- Name: schema_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_version (
    version character varying(20) NOT NULL,
    applied_at timestamp with time zone DEFAULT now()
);


--
-- Name: scoring_components; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scoring_components (
    component_id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(50),
    description text,
    default_weight numeric
);


--
-- Name: sector_sentiment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sector_sentiment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sector_id uuid,
    sentiment_score numeric,
    confidence numeric,
    calculated_at timestamp with time zone
);


--
-- Name: sectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sectors (
    sector_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text
);


--
-- Name: signal_factors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_factors (
    signal_factor_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    factor_name character varying(100),
    factor_score numeric,
    factor_weight numeric
);


--
-- Name: signal_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_history (
    history_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid,
    overall_score numeric,
    signal public.signal_type,
    trend public.trend_type,
    confidence numeric,
    reason text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signals (
    entity_id uuid NOT NULL,
    overall_score numeric,
    signal public.signal_type,
    trend public.trend_type,
    confidence numeric,
    last_reason text
);


--
-- Name: sync_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_jobs (
    sync_job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid,
    job_name character varying(100),
    last_run timestamp with time zone,
    status character varying(20)
);


--
-- Name: sync_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_log (
    sync_log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid,
    object_type character varying(50),
    status character varying(20),
    details jsonb
);


--
-- Name: sync_mapping; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_mapping (
    mapping_id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid,
    internal_type character varying(50),
    internal_id uuid,
    external_id character varying(255)
);


--
-- Name: system_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_jobs (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_name character varying(100),
    last_run timestamp with time zone,
    status character varying(20)
);


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    setting_key character varying(100) NOT NULL,
    setting_value text
);


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    tag_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    category character varying(50)
);


--
-- Name: user_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sources (
    source_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    source_type character varying(50) NOT NULL,
    source_name character varying(100) NOT NULL,
    external_id text,
    config jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying(100) NOT NULL,
    email character varying(255),
    display_name character varying(255),
    reporting_currency character(3) DEFAULT 'USD'::bpchar,
    timezone character varying(50) DEFAULT 'Europe/Rome'::character varying,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    password_hash text
);


--
-- Name: vw_entities; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_entities AS
 SELECT entity_id,
    name,
    entity_type,
    country_code,
    description,
    active,
    created_at,
    updated_at
   FROM public.entities e;


--
-- Name: vw_instruments; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_instruments AS
 SELECT i.instrument_id,
    i.entity_id,
    i.symbol,
    i.exchange,
    i.instrument_type,
    i.currency_code,
    e.name
   FROM (public.instruments i
     JOIN public.entities e ON ((e.entity_id = i.entity_id)));


--
-- Name: vw_portfolios; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_portfolios AS
 SELECT portfolio_id,
    user_id,
    name,
    portfolio_type,
    description,
    base_currency,
    created_at,
    updated_at
   FROM public.portfolios;


--
-- Name: watchlists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlists (
    watchlist_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vw_watchlists; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_watchlists AS
 SELECT watchlist_id,
    user_id,
    name,
    description,
    created_at,
    updated_at
   FROM public.watchlists;


--
-- Name: watchlist_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist_items (
    watchlist_item_id uuid DEFAULT gen_random_uuid() NOT NULL,
    watchlist_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    notes text,
    priority smallint,
    target_price numeric(20,8),
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT watchlist_items_priority_check CHECK (((priority IS NULL) OR ((priority >= 1) AND (priority <= 5))))
);


--
-- Name: api_sources api_sources_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_sources
    ADD CONSTRAINT api_sources_name_key UNIQUE (name);


--
-- Name: api_sources api_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_sources
    ADD CONSTRAINT api_sources_pkey PRIMARY KEY (api_source_id);


--
-- Name: catalyst_history catalyst_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalyst_history
    ADD CONSTRAINT catalyst_history_pkey PRIMARY KEY (id);


--
-- Name: catalysts catalysts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalysts
    ADD CONSTRAINT catalysts_pkey PRIMARY KEY (catalyst_id);


--
-- Name: entities entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_pkey PRIMARY KEY (entity_id);


--
-- Name: entity_aliases entity_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_aliases
    ADD CONSTRAINT entity_aliases_pkey PRIMARY KEY (alias_id);


--
-- Name: entity_metrics entity_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_metrics
    ADD CONSTRAINT entity_metrics_pkey PRIMARY KEY (metric_id);


--
-- Name: entity_relationships entity_relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_relationships
    ADD CONSTRAINT entity_relationships_pkey PRIMARY KEY (relationship_id);


--
-- Name: entity_scores entity_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_scores
    ADD CONSTRAINT entity_scores_pkey PRIMARY KEY (entity_score_id);


--
-- Name: entity_sectors entity_sectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_sectors
    ADD CONSTRAINT entity_sectors_pkey PRIMARY KEY (entity_id, sector_id);


--
-- Name: entity_tags entity_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_tags
    ADD CONSTRAINT entity_tags_pkey PRIMARY KEY (entity_id, tag_id);


--
-- Name: etf_constituents etf_constituents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_constituents
    ADD CONSTRAINT etf_constituents_pkey PRIMARY KEY (constituent_id);


--
-- Name: exchange_rates exchange_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT exchange_rates_pkey PRIMARY KEY (currency_code);


--
-- Name: exchanges exchanges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchanges
    ADD CONSTRAINT exchanges_pkey PRIMARY KEY (exchange);


--
-- Name: external_systems external_systems_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.external_systems
    ADD CONSTRAINT external_systems_name_key UNIQUE (name);


--
-- Name: external_systems external_systems_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.external_systems
    ADD CONSTRAINT external_systems_pkey PRIMARY KEY (system_id);


--
-- Name: filings filings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filings
    ADD CONSTRAINT filings_pkey PRIMARY KEY (filing_id);


--
-- Name: entity_impact_history impact_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_impact_history
    ADD CONSTRAINT impact_history_pkey PRIMARY KEY (id);


--
-- Name: insider_transactions insider_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insider_transactions
    ADD CONSTRAINT insider_transactions_pkey PRIMARY KEY (id);


--
-- Name: instrument_aliases instrument_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_aliases
    ADD CONSTRAINT instrument_aliases_pkey PRIMARY KEY (alias_id);


--
-- Name: instrument_entity_exposure instrument_exposure_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_entity_exposure
    ADD CONSTRAINT instrument_exposure_pkey PRIMARY KEY (exposure_id);


--
-- Name: instrument_relations instrument_relations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_relations
    ADD CONSTRAINT instrument_relations_pkey PRIMARY KEY (relation_id);


--
-- Name: instruments instruments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_pkey PRIMARY KEY (instrument_id);


--
-- Name: instruments instruments_symbol_exchange_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_symbol_exchange_key UNIQUE (symbol, exchange);


--
-- Name: market_search_cache market_search_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_search_cache
    ADD CONSTRAINT market_search_cache_pkey PRIMARY KEY (cache_key);


--
-- Name: news_clusters news_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_clusters
    ADD CONSTRAINT news_clusters_pkey PRIMARY KEY (cluster_id);


--
-- Name: news news_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_content_hash_key UNIQUE (content_hash);


--
-- Name: news_entities news_entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_entities
    ADD CONSTRAINT news_entities_pkey PRIMARY KEY (news_entity_id);


--
-- Name: news_impacts news_impacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_impacts
    ADD CONSTRAINT news_impacts_pkey PRIMARY KEY (impact_id);


--
-- Name: news news_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_pkey PRIMARY KEY (news_id);


--
-- Name: news_sources news_sources_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_sources
    ADD CONSTRAINT news_sources_name_key UNIQUE (name);


--
-- Name: news_sources news_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_sources
    ADD CONSTRAINT news_sources_pkey PRIMARY KEY (source_id);


--
-- Name: notification_hashes notification_hashes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_hashes
    ADD CONSTRAINT notification_hashes_pkey PRIMARY KEY (hash);


--
-- Name: notification_rules notification_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_rules
    ADD CONSTRAINT notification_rules_pkey PRIMARY KEY (rule_id);


--
-- Name: notification_subscriptions notification_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_pkey PRIMARY KEY (subscription_id);


--
-- Name: notification_templates notification_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_templates
    ADD CONSTRAINT notification_templates_pkey PRIMARY KEY (template_id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (notification_id);


--
-- Name: cache_portfolio_exposure portfolio_exposure_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_exposure
    ADD CONSTRAINT portfolio_exposure_cache_pkey PRIMARY KEY (cache_id);


--
-- Name: portfolio_holdings_snapshots portfolio_holdings_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings_snapshots
    ADD CONSTRAINT portfolio_holdings_snapshots_pkey PRIMARY KEY (holding_snapshot_id);


--
-- Name: portfolio_impacts portfolio_impacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_impacts
    ADD CONSTRAINT portfolio_impacts_pkey PRIMARY KEY (impact_id);


--
-- Name: cache_portfolio_sector_exposure portfolio_sector_exposure_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_sector_exposure
    ADD CONSTRAINT portfolio_sector_exposure_pkey PRIMARY KEY (id);


--
-- Name: portfolio_snapshots portfolio_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_snapshots
    ADD CONSTRAINT portfolio_snapshots_pkey PRIMARY KEY (snapshot_id);


--
-- Name: cache_portfolio_theme_exposure portfolio_theme_exposure_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_theme_exposure
    ADD CONSTRAINT portfolio_theme_exposure_pkey PRIMARY KEY (id);


--
-- Name: portfolios portfolios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_pkey PRIMARY KEY (portfolio_id);


--
-- Name: positions positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_pkey PRIMARY KEY (position_id);


--
-- Name: price_history price_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_pkey PRIMARY KEY (price_id);


--
-- Name: scheduler_job_runs scheduler_job_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_job_runs
    ADD CONSTRAINT scheduler_job_runs_pkey PRIMARY KEY (job_run_id);


--
-- Name: scheduler_jobs scheduler_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_jobs
    ADD CONSTRAINT scheduler_jobs_pkey PRIMARY KEY (job_id);


--
-- Name: schema_version schema_version_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_version
    ADD CONSTRAINT schema_version_pkey PRIMARY KEY (version);


--
-- Name: scoring_components scoring_components_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scoring_components
    ADD CONSTRAINT scoring_components_code_key UNIQUE (code);


--
-- Name: scoring_components scoring_components_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scoring_components
    ADD CONSTRAINT scoring_components_pkey PRIMARY KEY (component_id);


--
-- Name: sector_sentiment sector_sentiment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sector_sentiment
    ADD CONSTRAINT sector_sentiment_pkey PRIMARY KEY (id);


--
-- Name: sectors sectors_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sectors
    ADD CONSTRAINT sectors_name_key UNIQUE (name);


--
-- Name: sectors sectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sectors
    ADD CONSTRAINT sectors_pkey PRIMARY KEY (sector_id);


--
-- Name: signal_factors signal_factors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_factors
    ADD CONSTRAINT signal_factors_pkey PRIMARY KEY (signal_factor_id);


--
-- Name: signal_history signal_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_history
    ADD CONSTRAINT signal_history_pkey PRIMARY KEY (history_id);


--
-- Name: signals signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals
    ADD CONSTRAINT signals_pkey PRIMARY KEY (entity_id);


--
-- Name: sync_jobs sync_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT sync_jobs_pkey PRIMARY KEY (sync_job_id);


--
-- Name: sync_log sync_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_log
    ADD CONSTRAINT sync_log_pkey PRIMARY KEY (sync_log_id);


--
-- Name: sync_mapping sync_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_mapping
    ADD CONSTRAINT sync_mapping_pkey PRIMARY KEY (mapping_id);


--
-- Name: system_jobs system_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_jobs
    ADD CONSTRAINT system_jobs_pkey PRIMARY KEY (job_id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (setting_key);


--
-- Name: tags tags_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_name_key UNIQUE (name);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (tag_id);


--
-- Name: users uq_users_email; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_email UNIQUE (email);


--
-- Name: users uq_users_username; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_username UNIQUE (username);


--
-- Name: user_sources user_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sources
    ADD CONSTRAINT user_sources_pkey PRIMARY KEY (source_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: watchlist_items watchlist_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_pkey PRIMARY KEY (watchlist_item_id);


--
-- Name: watchlists watchlists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_pkey PRIMARY KEY (watchlist_id);


--
-- Name: idx_entities_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entities_name_trgm ON public.entities USING gin (name public.gin_trgm_ops);


--
-- Name: idx_instruments_symbol_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_instruments_symbol_trgm ON public.instruments USING gin (symbol public.gin_trgm_ops);


--
-- Name: idx_market_search_cache_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_market_search_cache_created ON public.market_search_cache USING btree (cached_at);


--
-- Name: ix_holding_snapshots_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_holding_snapshots_date ON public.portfolio_holdings_snapshots USING btree (snapshot_date);


--
-- Name: ix_holding_snapshots_instrument; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_holding_snapshots_instrument ON public.portfolio_holdings_snapshots USING btree (instrument_id);


--
-- Name: ix_holding_snapshots_portfolio; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_holding_snapshots_portfolio ON public.portfolio_holdings_snapshots USING btree (portfolio_id);


--
-- Name: ix_notification_subscriptions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_subscriptions_user ON public.notification_subscriptions USING btree (user_id, enabled);


--
-- Name: ix_notifications_delivery_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_delivery_queue ON public.notifications USING btree (status, next_attempt_at, created_at) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RETRY'::character varying])::text[]));


--
-- Name: ix_notifications_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_source_id ON public.notifications USING btree (source_id);


--
-- Name: ix_notifications_user_branch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_branch ON public.notifications USING btree (user_id, branch);


--
-- Name: ix_notifications_user_sent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_sent ON public.notifications USING btree (user_id, sent_at DESC);


--
-- Name: ix_notifications_user_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_symbol ON public.notifications USING btree (user_id, symbol);


--
-- Name: ix_portfolio_snapshots_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_portfolio_snapshots_date ON public.portfolio_snapshots USING btree (snapshot_date);


--
-- Name: ix_portfolio_snapshots_portfolio; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_portfolio_snapshots_portfolio ON public.portfolio_snapshots USING btree (portfolio_id);


--
-- Name: ix_portfolios_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_portfolios_user ON public.portfolios USING btree (user_id);


--
-- Name: ix_positions_instrument; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_positions_instrument ON public.positions USING btree (instrument_id);


--
-- Name: ix_positions_portfolio; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_positions_portfolio ON public.positions USING btree (portfolio_id);


--
-- Name: ix_user_sources_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_sources_user ON public.user_sources USING btree (user_id);


--
-- Name: ix_users_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_active ON public.users USING btree (is_active);


--
-- Name: ix_watchlist_items_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_watchlist_items_entity ON public.watchlist_items USING btree (entity_id);


--
-- Name: ix_watchlist_items_watchlist; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_watchlist_items_watchlist ON public.watchlist_items USING btree (watchlist_id);


--
-- Name: ix_watchlists_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_watchlists_user ON public.watchlists USING btree (user_id);


--
-- Name: uq_cache_portfolio_exposure; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cache_portfolio_exposure ON public.cache_portfolio_exposure USING btree (portfolio_id, entity_id);


--
-- Name: uq_cache_portfolio_sector_exposure; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cache_portfolio_sector_exposure ON public.cache_portfolio_sector_exposure USING btree (portfolio_id, sector_id);


--
-- Name: uq_cache_portfolio_theme_exposure; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cache_portfolio_theme_exposure ON public.cache_portfolio_theme_exposure USING btree (portfolio_id, tag_id);


--
-- Name: uq_catalyst; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_catalyst ON public.catalysts USING btree (entity_id, catalyst_type, catalyst_date);


--
-- Name: uq_entity_alias; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_alias ON public.entity_aliases USING btree (alias_name);


--
-- Name: uq_entity_metric; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_metric ON public.entity_metrics USING btree (entity_id, metric_name, metric_date);


--
-- Name: uq_entity_score; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_score ON public.entity_scores USING btree (entity_id, component_id);


--
-- Name: uq_filings; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_filings ON public.filings USING btree (cik, accepted_at, filing_type);


--
-- Name: uq_holding_snapshot; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_holding_snapshot ON public.portfolio_holdings_snapshots USING btree (portfolio_id, instrument_id, snapshot_date);


--
-- Name: uq_instrument_alias; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_instrument_alias ON public.instrument_aliases USING btree (alias_symbol);


--
-- Name: uq_news_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_news_entity ON public.news_entities USING btree (news_id, entity_id);


--
-- Name: uq_news_impact; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_news_impact ON public.news_impacts USING btree (news_id, entity_id);


--
-- Name: uq_notifications_user_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_notifications_user_key ON public.notifications USING btree (user_id, notification_key) WHERE (notification_key IS NOT NULL);


--
-- Name: uq_portfolio_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_portfolio_name ON public.portfolios USING btree (user_id, name);


--
-- Name: uq_portfolio_snapshot; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_portfolio_snapshot ON public.portfolio_snapshots USING btree (portfolio_id, snapshot_date);


--
-- Name: uq_position; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_position ON public.positions USING btree (portfolio_id, instrument_id);


--
-- Name: uq_user_source; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_user_source ON public.user_sources USING btree (user_id, source_type, source_name);


--
-- Name: uq_watchlist_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_watchlist_item ON public.watchlist_items USING btree (watchlist_id, entity_id);


--
-- Name: uq_watchlist_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_watchlist_name ON public.watchlists USING btree (user_id, name);


--
-- Name: ux_scheduler_jobs_job_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_scheduler_jobs_job_key ON public.scheduler_jobs USING btree (job_key);


--
-- Name: entities trg_entities_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entities_created_at BEFORE INSERT ON public.entities FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: entities trg_entities_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entities_updated_at BEFORE INSERT OR UPDATE ON public.entities FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: entity_impact_history trg_entity_impact_history_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entity_impact_history_created_at BEFORE INSERT ON public.entity_impact_history FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: exchange_rates trg_exchange_rates_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exchange_rates_updated_at BEFORE INSERT OR UPDATE ON public.exchange_rates FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: exchanges trg_exchanges_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exchanges_created_at BEFORE INSERT ON public.exchanges FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: exchanges trg_exchanges_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exchanges_updated_at BEFORE INSERT OR UPDATE ON public.exchanges FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: filings trg_filings_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_filings_created_at BEFORE INSERT ON public.filings FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: instruments trg_instruments_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_instruments_created_at BEFORE INSERT ON public.instruments FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: instruments trg_instruments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_instruments_updated_at BEFORE INSERT OR UPDATE ON public.instruments FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: news_clusters trg_news_clusters_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_news_clusters_created_at BEFORE INSERT ON public.news_clusters FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: notifications trg_notifications_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notifications_created_at BEFORE INSERT ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: notifications trg_notifications_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notifications_updated_at BEFORE INSERT OR UPDATE ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: portfolio_holdings_snapshots trg_portfolio_holdings_snapshots_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_portfolio_holdings_snapshots_created_at BEFORE INSERT ON public.portfolio_holdings_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: portfolio_snapshots trg_portfolio_snapshots_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_portfolio_snapshots_created_at BEFORE INSERT ON public.portfolio_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: portfolios trg_portfolios_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_portfolios_created_at BEFORE INSERT ON public.portfolios FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: portfolios trg_portfolios_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_portfolios_updated_at BEFORE INSERT OR UPDATE ON public.portfolios FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: positions trg_positions_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_positions_created_at BEFORE INSERT ON public.positions FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: positions trg_positions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_positions_updated_at BEFORE INSERT OR UPDATE ON public.positions FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: scheduler_jobs trg_scheduler_jobs_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scheduler_jobs_created_at BEFORE INSERT ON public.scheduler_jobs FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: scheduler_jobs trg_scheduler_jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scheduler_jobs_updated_at BEFORE INSERT OR UPDATE ON public.scheduler_jobs FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: signal_history trg_signal_history_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_signal_history_created_at BEFORE INSERT ON public.signal_history FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: user_sources trg_user_sources_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_sources_created_at BEFORE INSERT ON public.user_sources FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: users trg_users_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_created_at BEFORE INSERT ON public.users FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE INSERT OR UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: watchlists trg_watchlists_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_watchlists_created_at BEFORE INSERT ON public.watchlists FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: watchlists trg_watchlists_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_watchlists_updated_at BEFORE INSERT OR UPDATE ON public.watchlists FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: catalyst_history catalyst_history_catalyst_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalyst_history
    ADD CONSTRAINT catalyst_history_catalyst_id_fkey FOREIGN KEY (catalyst_id) REFERENCES public.catalysts(catalyst_id) ON DELETE CASCADE;


--
-- Name: catalysts catalysts_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalysts
    ADD CONSTRAINT catalysts_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_aliases entity_aliases_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_aliases
    ADD CONSTRAINT entity_aliases_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_metrics entity_metrics_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_metrics
    ADD CONSTRAINT entity_metrics_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_relationships entity_relationships_source_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_relationships
    ADD CONSTRAINT entity_relationships_source_entity_id_fkey FOREIGN KEY (source_entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_relationships entity_relationships_target_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_relationships
    ADD CONSTRAINT entity_relationships_target_entity_id_fkey FOREIGN KEY (target_entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_scores entity_scores_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_scores
    ADD CONSTRAINT entity_scores_component_id_fkey FOREIGN KEY (component_id) REFERENCES public.scoring_components(component_id);


--
-- Name: entity_scores entity_scores_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_scores
    ADD CONSTRAINT entity_scores_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_sectors entity_sectors_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_sectors
    ADD CONSTRAINT entity_sectors_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_sectors entity_sectors_sector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_sectors
    ADD CONSTRAINT entity_sectors_sector_id_fkey FOREIGN KEY (sector_id) REFERENCES public.sectors(sector_id) ON DELETE CASCADE;


--
-- Name: entity_tags entity_tags_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_tags
    ADD CONSTRAINT entity_tags_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_tags entity_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_tags
    ADD CONSTRAINT entity_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE;


--
-- Name: etf_constituents etf_constituents_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_constituents
    ADD CONSTRAINT etf_constituents_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: etf_constituents etf_constituents_etf_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_constituents
    ADD CONSTRAINT etf_constituents_etf_instrument_id_fkey FOREIGN KEY (etf_instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: filings filings_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filings
    ADD CONSTRAINT filings_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_impact_history impact_history_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_impact_history
    ADD CONSTRAINT impact_history_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: insider_transactions insider_transactions_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insider_transactions
    ADD CONSTRAINT insider_transactions_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: instrument_aliases instrument_aliases_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_aliases
    ADD CONSTRAINT instrument_aliases_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE CASCADE;


--
-- Name: instrument_entity_exposure instrument_exposure_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_entity_exposure
    ADD CONSTRAINT instrument_exposure_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: instrument_entity_exposure instrument_exposure_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_entity_exposure
    ADD CONSTRAINT instrument_exposure_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: instrument_relations instrument_relations_child_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_relations
    ADD CONSTRAINT instrument_relations_child_instrument_id_fkey FOREIGN KEY (child_instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: instrument_relations instrument_relations_parent_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_relations
    ADD CONSTRAINT instrument_relations_parent_instrument_id_fkey FOREIGN KEY (parent_instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: instruments instruments_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: news news_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.news_clusters(cluster_id);


--
-- Name: news_entities news_entities_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_entities
    ADD CONSTRAINT news_entities_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: news_entities news_entities_news_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_entities
    ADD CONSTRAINT news_entities_news_id_fkey FOREIGN KEY (news_id) REFERENCES public.news(news_id) ON DELETE CASCADE;


--
-- Name: news_impacts news_impacts_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_impacts
    ADD CONSTRAINT news_impacts_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: news_impacts news_impacts_news_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_impacts
    ADD CONSTRAINT news_impacts_news_id_fkey FOREIGN KEY (news_id) REFERENCES public.news(news_id);


--
-- Name: news news_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.news_sources(source_id);


--
-- Name: notification_hashes notification_hashes_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_hashes
    ADD CONSTRAINT notification_hashes_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: notification_subscriptions notification_subscriptions_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.user_sources(source_id) ON DELETE CASCADE;


--
-- Name: notification_subscriptions notification_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: notifications notifications_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: notifications notifications_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.user_sources(source_id) ON DELETE SET NULL;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: cache_portfolio_exposure portfolio_exposure_cache_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_exposure
    ADD CONSTRAINT portfolio_exposure_cache_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: portfolio_holdings_snapshots portfolio_holdings_snapshots_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings_snapshots
    ADD CONSTRAINT portfolio_holdings_snapshots_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT;


--
-- Name: portfolio_holdings_snapshots portfolio_holdings_snapshots_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings_snapshots
    ADD CONSTRAINT portfolio_holdings_snapshots_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(portfolio_id) ON DELETE CASCADE;


--
-- Name: portfolio_impacts portfolio_impacts_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_impacts
    ADD CONSTRAINT portfolio_impacts_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: cache_portfolio_sector_exposure portfolio_sector_exposure_sector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_sector_exposure
    ADD CONSTRAINT portfolio_sector_exposure_sector_id_fkey FOREIGN KEY (sector_id) REFERENCES public.sectors(sector_id);


--
-- Name: portfolio_snapshots portfolio_snapshots_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_snapshots
    ADD CONSTRAINT portfolio_snapshots_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(portfolio_id) ON DELETE CASCADE;


--
-- Name: cache_portfolio_theme_exposure portfolio_theme_exposure_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cache_portfolio_theme_exposure
    ADD CONSTRAINT portfolio_theme_exposure_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id);


--
-- Name: portfolios portfolios_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: positions positions_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT;


--
-- Name: positions positions_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(portfolio_id) ON DELETE CASCADE;


--
-- Name: price_history price_history_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: sector_sentiment sector_sentiment_sector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sector_sentiment
    ADD CONSTRAINT sector_sentiment_sector_id_fkey FOREIGN KEY (sector_id) REFERENCES public.sectors(sector_id);


--
-- Name: signal_factors signal_factors_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_factors
    ADD CONSTRAINT signal_factors_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: signal_history signal_history_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_history
    ADD CONSTRAINT signal_history_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: signals signals_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals
    ADD CONSTRAINT signals_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: sync_jobs sync_jobs_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT sync_jobs_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id);


--
-- Name: sync_log sync_log_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_log
    ADD CONSTRAINT sync_log_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id);


--
-- Name: sync_mapping sync_mapping_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_mapping
    ADD CONSTRAINT sync_mapping_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id);


--
-- Name: user_sources user_sources_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sources
    ADD CONSTRAINT user_sources_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: watchlist_items watchlist_items_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE RESTRICT;


--
-- Name: watchlist_items watchlist_items_watchlist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES public.watchlists(watchlist_id) ON DELETE CASCADE;


--
-- Name: watchlists watchlists_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 6fNxrOEk96Qv79xD2Aypmf84MYF65NxqQYlDZKQUIpKLz43nlz3se8C0FwQTtVq

