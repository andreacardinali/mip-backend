--
-- PostgreSQL database dump
--

\restrict TeI66ejnKfBnx9GixkEJl155dqsShvwz2ld0UAqUaL6CNMhLhsWGXtNIpZVkGhb

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
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: EXTENSION unaccent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION unaccent IS 'text search dictionary that removes accents';


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
-- Name: fn_alert_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_alert_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN NEW.updated_at=clock_timestamp();RETURN NEW;END $$;


--
-- Name: fn_market_screener_touch_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_market_screener_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


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
-- Name: fx_convert_at_date(numeric, character, character, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fx_convert_at_date(p_amount numeric, p_from character, p_to character, p_rate_date date) RETURNS numeric
    LANGUAGE plpgsql STABLE
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

    IF p_rate_date IS NULL THEN
        RAISE EXCEPTION 'p_rate_date is required';
    END IF;

    v_from_currency := upper(btrim(p_from));
    v_to_currency := upper(btrim(p_to));

    IF v_from_currency = v_to_currency THEN
        RETURN p_amount;
    END IF;

    SELECT h.rate_to_usd
    INTO v_from_rate
    FROM public.exchange_rate_history h
    WHERE h.currency_code = v_from_currency
      AND h.rate_date <= p_rate_date
    ORDER BY h.rate_date DESC
    LIMIT 1;

    SELECT h.rate_to_usd
    INTO v_to_rate
    FROM public.exchange_rate_history h
    WHERE h.currency_code = v_to_currency
      AND h.rate_date <= p_rate_date
    ORDER BY h.rate_date DESC
    LIMIT 1;

    IF v_from_rate IS NULL THEN
        RAISE EXCEPTION 'Historical exchange rate not found for % at %',
            v_from_currency, p_rate_date;
    END IF;

    IF v_to_rate IS NULL THEN
        RAISE EXCEPTION 'Historical exchange rate not found for % at %',
            v_to_currency, p_rate_date;
    END IF;

    RETURN (p_amount * v_from_rate) / v_to_rate;
END;
$$;


--
-- Name: position_cost_basis(numeric, numeric, character, numeric, character, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.position_cost_basis(p_quantity numeric, p_avg_price numeric, p_listing_currency character, p_cost_amount numeric, p_cost_currency character, p_target_currency character) RETURNS numeric
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


--
-- Name: sp_alert_list_enabled_rules(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_alert_list_enabled_rules() RETURNS TABLE(rule_id uuid, user_id uuid, name character varying, alert_type character varying, severity character varying, channel character varying, conditions jsonb, cooldown_minutes integer)
    LANGUAGE sql STABLE
    AS $$ SELECT r.rule_id,r.user_id,r.name,r.alert_type,r.severity,r.channel,r.conditions,r.cooldown_minutes FROM public.alert_rules r WHERE r.enabled ORDER BY r.user_id,r.name $$;


--
-- Name: sp_alert_list_user_rules(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_alert_list_rule_candidates(p_rule_id uuid, p_limit integer)
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

CREATE FUNCTION public.sp_alert_list_user_rules(p_user_id uuid) RETURNS TABLE(rule_id uuid, name character varying, alert_type character varying, severity character varying, channel character varying, conditions jsonb, cooldown_minutes integer, enabled boolean)
    LANGUAGE sql STABLE
    AS $$
    SELECT r.rule_id, r.name, r.alert_type, r.severity, r.channel,
           r.conditions, r.cooldown_minutes, r.enabled
    FROM public.alert_rules r
    WHERE r.user_id = p_user_id
    ORDER BY r.name, r.rule_id
$$;


--
-- Name: sp_alert_mark_enqueued(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_alert_mark_enqueued(p_event_id uuid, p_notification_id uuid) RETURNS void
    LANGUAGE sql
    AS $$ UPDATE public.alert_events SET notification_id=p_notification_id WHERE alert_event_id=p_event_id $$;


--
-- Name: sp_alert_register_match(uuid, uuid, uuid, character varying, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_alert_register_match(p_rule_id uuid, p_instrument_id uuid, p_signal_id uuid, p_fingerprint character varying, p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$ DECLARE r public.alert_rules%ROWTYPE; e public.alert_events%ROWTYPE; v uuid; BEGIN SELECT * INTO r FROM public.alert_rules WHERE rule_id=p_rule_id AND enabled FOR SHARE; IF NOT FOUND THEN RETURN NULL;END IF; SELECT * INTO e FROM public.alert_events WHERE rule_id=p_rule_id AND instrument_id=p_instrument_id ORDER BY created_at DESC LIMIT 1; IF FOUND AND (e.fingerprint=p_fingerprint OR e.created_at>now()-make_interval(mins=>r.cooldown_minutes)) THEN RETURN NULL;END IF; INSERT INTO public.alert_events(rule_id,user_id,instrument_id,signal_id,fingerprint,payload) VALUES(p_rule_id,r.user_id,p_instrument_id,p_signal_id,p_fingerprint,coalesce(p_payload,'{}')) ON CONFLICT(rule_id,instrument_id,fingerprint) DO NOTHING RETURNING alert_event_id INTO v; RETURN v; END $$;


--
-- Name: sp_alert_upsert_rule(uuid, uuid, character varying, character varying, character varying, character varying, jsonb, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_alert_upsert_rule(p_rule_id uuid, p_user_id uuid, p_name character varying, p_alert_type character varying, p_severity character varying, p_channel character varying, p_conditions jsonb, p_cooldown_minutes integer, p_enabled boolean) RETURNS uuid
    LANGUAGE plpgsql
    AS $$ DECLARE v uuid; BEGIN IF p_rule_id IS NULL THEN INSERT INTO public.alert_rules(user_id,name,alert_type,severity,channel,conditions,cooldown_minutes,enabled) VALUES(p_user_id,btrim(p_name),upper(coalesce(p_alert_type,'MARKET_SIGNAL')),upper(coalesce(p_severity,'MEDIUM')),nullif(upper(btrim(p_channel)),''),coalesce(p_conditions,'{}'),greatest(coalesce(p_cooldown_minutes,240),0),coalesce(p_enabled,true)) ON CONFLICT(user_id,name) DO UPDATE SET alert_type=excluded.alert_type,severity=excluded.severity,channel=excluded.channel,conditions=excluded.conditions,cooldown_minutes=excluded.cooldown_minutes,enabled=excluded.enabled RETURNING rule_id INTO v; ELSE UPDATE public.alert_rules SET name=btrim(p_name),alert_type=upper(coalesce(p_alert_type,'MARKET_SIGNAL')),severity=upper(coalesce(p_severity,'MEDIUM')),channel=nullif(upper(btrim(p_channel)),''),conditions=coalesce(p_conditions,'{}'),cooldown_minutes=greatest(coalesce(p_cooldown_minutes,240),0),enabled=coalesce(p_enabled,true) WHERE rule_id=p_rule_id AND user_id=p_user_id RETURNING rule_id INTO v; END IF; RETURN v; END $$;


--
-- Name: sp_apply_instrument_reference_metadata(uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_apply_instrument_reference_metadata(p_instrument_id uuid, p_payload jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source_code varchar;
    v_identifiers jsonb;
    v_item record;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM instruments WHERE instrument_id = p_instrument_id
    ) THEN
        RAISE EXCEPTION 'Instrument not found: %', p_instrument_id;
    END IF;

    v_source_code := UPPER(NULLIF(TRIM(COALESCE(
        p_payload->>'source',
        p_payload->>'data_source',
        p_payload->>'metadata_source'
    )), ''));

    v_identifiers := COALESCE(p_payload->'identifiers', '{}'::jsonb)
        || jsonb_strip_nulls(jsonb_build_object(
            'ISIN', p_payload->>'isin',
            'WKN', p_payload->>'wkn',
            'FIGI', p_payload->>'figi',
            'FIGI_COMPOSITE', p_payload->>'figi_composite',
            'FIGI_SHARE_CLASS', p_payload->>'figi_share_class'
        ));

    FOR v_item IN
        SELECT key, value #>> '{}' AS value
        FROM jsonb_each(v_identifiers)
    LOOP
        IF EXISTS (
            SELECT 1 FROM identity_types
            WHERE code = UPPER(v_item.key)
              AND target_kind = 'INSTRUMENT'
        ) THEN
            PERFORM sp_upsert_identity(
                'INSTRUMENT',
                p_instrument_id,
                UPPER(v_item.key),
                v_item.value,
                v_source_code,
                NULL,
                NULL,
                p_payload
            );
        END IF;
    END LOOP;

    UPDATE instruments i
    SET price_source_id = COALESCE(
            (
                SELECT es.system_id
                FROM external_systems es
                WHERE es.code = UPPER(NULLIF(TRIM(COALESCE(
                    p_payload->>'price_source',
                    p_payload->>'data_source'
                )), ''))
            ),
            i.price_source_id
        ),
        price_as_of = COALESCE(
            to_timestamp(NULLIF(p_payload->>'price_timestamp', '')::double precision),
            i.price_as_of
        )
    WHERE i.instrument_id = p_instrument_id;
END;
$$;


--
-- Name: sp_claim_llm_request(uuid, character varying, integer, integer, integer, numeric, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_claim_llm_request(p_request_id uuid, p_worker_id character varying, p_tokens_per_minute integer, p_requests_per_minute integer, p_requests_per_day integer, p_safety_factor numeric, p_daily_reset_timezone character varying) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_queue_key varchar(400);
    v_estimated_tokens integer;
    v_token_budget integer;
    v_request_budget integer;
    v_daily_budget integer;
    v_quota_day date;
    v_next_quota_reset timestamptz;
    v_window public.llm_rate_limit_windows%ROWTYPE;
BEGIN
    IF p_safety_factor <= 0 OR p_safety_factor > 1 THEN
        RAISE EXCEPTION 'p_safety_factor must be in (0, 1]';
    END IF;

    IF NULLIF(btrim(p_daily_reset_timezone), '') IS NULL THEN
        RAISE EXCEPTION 'p_daily_reset_timezone cannot be empty';
    END IF;

    v_quota_day :=
        (now() AT TIME ZONE p_daily_reset_timezone)::date;
    v_next_quota_reset :=
        ((v_quota_day + 1)::timestamp AT TIME ZONE p_daily_reset_timezone);

    SELECT queue_key, estimated_tokens
    INTO v_queue_key, v_estimated_tokens
    FROM public.llm_request_queue
    WHERE request_id = p_request_id
      AND status IN ('PENDING', 'RETRY')
      AND available_at <= now()
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN false;
    END IF;

    INSERT INTO public.llm_rate_limit_windows(
        queue_key,
        quota_day
    )
    VALUES (
        v_queue_key,
        v_quota_day
    )
    ON CONFLICT (queue_key) DO NOTHING;

    SELECT *
    INTO v_window
    FROM public.llm_rate_limit_windows
    WHERE queue_key = v_queue_key
    FOR UPDATE;

    IF now() - v_window.window_started_at >= interval '60 seconds' THEN
        v_window.window_started_at := now();
        v_window.tokens_used := 0;
        v_window.requests_used := 0;
    END IF;

    IF v_window.quota_day IS DISTINCT FROM v_quota_day THEN
        v_window.quota_day := v_quota_day;
        v_window.requests_used_today := 0;
    END IF;

    IF v_window.blocked_until IS NOT NULL
       AND v_window.blocked_until > now()
    THEN
        RETURN false;
    END IF;

    v_token_budget := floor(
        GREATEST(p_tokens_per_minute, 0) * p_safety_factor
    );
    v_request_budget := floor(
        GREATEST(p_requests_per_minute, 0) * p_safety_factor
    );
    v_daily_budget := floor(
        GREATEST(p_requests_per_day, 0) * p_safety_factor
    );

    IF v_daily_budget > 0
       AND v_window.requests_used_today + 1 > v_daily_budget
    THEN
        UPDATE public.llm_request_queue
        SET available_at = v_next_quota_reset
        WHERE request_id = p_request_id;
        RETURN false;
    END IF;

    IF (v_token_budget > 0
        AND v_window.tokens_used + v_estimated_tokens > v_token_budget)
       OR (v_request_budget > 0
           AND v_window.requests_used + 1 > v_request_budget)
    THEN
        UPDATE public.llm_request_queue
        SET available_at = v_window.window_started_at + interval '60 seconds'
        WHERE request_id = p_request_id;
        RETURN false;
    END IF;

    UPDATE public.llm_rate_limit_windows
    SET window_started_at = v_window.window_started_at,
        tokens_used = v_window.tokens_used + v_estimated_tokens,
        requests_used = v_window.requests_used + 1,
        quota_day = v_window.quota_day,
        requests_used_today = v_window.requests_used_today + 1
    WHERE queue_key = v_queue_key;

    UPDATE public.llm_request_queue
    SET status = 'PROCESSING',
        locked_at = now(),
        locked_by = p_worker_id,
        attempt_count = attempt_count + 1,
        last_error = NULL
    WHERE request_id = p_request_id;

    RETURN true;
END;
$$;


--
-- Name: sp_claim_llm_request(uuid, character varying, integer, integer, integer, numeric, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_claim_llm_request(p_request_id uuid, p_worker_id character varying, p_tokens_per_minute integer, p_requests_per_minute integer, p_requests_per_day integer, p_safety_factor numeric, p_daily_reset_timezone character varying, p_lease_seconds integer) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_queue_key varchar(400);
    v_estimated_tokens integer;
    v_token_budget integer;
    v_request_budget integer;
    v_daily_budget integer;
    v_quota_day date;
    v_next_quota_reset timestamptz;
    v_window public.llm_rate_limit_windows%ROWTYPE;
BEGIN
    IF p_safety_factor <= 0 OR p_safety_factor > 1 THEN
        RAISE EXCEPTION 'p_safety_factor must be in (0, 1]';
    END IF;

    IF NULLIF(btrim(p_daily_reset_timezone), '') IS NULL THEN
        RAISE EXCEPTION 'p_daily_reset_timezone cannot be empty';
    END IF;

    v_quota_day :=
        (now() AT TIME ZONE p_daily_reset_timezone)::date;
    v_next_quota_reset :=
        ((v_quota_day + 1)::timestamp AT TIME ZONE p_daily_reset_timezone);

    SELECT queue_key, estimated_tokens
    INTO v_queue_key, v_estimated_tokens
    FROM public.llm_request_queue
    WHERE request_id = p_request_id
      AND status IN ('PENDING', 'RETRY')
      AND available_at <= now()
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.llm_request_queue q
        WHERE q.queue_key = v_queue_key
          AND q.status IN ('PENDING', 'RETRY')
          AND q.available_at <= now()
          AND (q.priority, q.created_at, q.request_id)
              < (
                  SELECT priority, created_at, request_id
                  FROM public.llm_request_queue
                  WHERE request_id = p_request_id
              )
    ) THEN
        RETURN false;
    END IF;

    INSERT INTO public.llm_rate_limit_windows(
        queue_key,
        quota_day
    )
    VALUES (
        v_queue_key,
        v_quota_day
    )
    ON CONFLICT (queue_key) DO NOTHING;

    SELECT *
    INTO v_window
    FROM public.llm_rate_limit_windows
    WHERE queue_key = v_queue_key
    FOR UPDATE;

    IF now() - v_window.window_started_at >= interval '60 seconds' THEN
        v_window.window_started_at := now();
        v_window.tokens_used := 0;
        v_window.requests_used := 0;
    END IF;

    IF v_window.quota_day IS DISTINCT FROM v_quota_day THEN
        v_window.quota_day := v_quota_day;
        v_window.requests_used_today := 0;
    END IF;

    IF v_window.blocked_until IS NOT NULL
       AND v_window.blocked_until > now()
    THEN
        RETURN false;
    END IF;

    v_token_budget := floor(
        GREATEST(p_tokens_per_minute, 0) * p_safety_factor
    );
    v_request_budget := floor(
        GREATEST(p_requests_per_minute, 0) * p_safety_factor
    );
    v_daily_budget := floor(
        GREATEST(p_requests_per_day, 0) * p_safety_factor
    );

    IF v_daily_budget > 0
       AND v_window.requests_used_today + 1 > v_daily_budget
    THEN
        UPDATE public.llm_request_queue
        SET available_at = v_next_quota_reset
        WHERE request_id = p_request_id;
        RETURN false;
    END IF;

    IF (v_token_budget > 0
        AND v_window.tokens_used + v_estimated_tokens > v_token_budget)
       OR (v_request_budget > 0
           AND v_window.requests_used + 1 > v_request_budget)
    THEN
        UPDATE public.llm_request_queue
        SET available_at = v_window.window_started_at + interval '60 seconds'
        WHERE request_id = p_request_id;
        RETURN false;
    END IF;

    UPDATE public.llm_rate_limit_windows
    SET window_started_at = v_window.window_started_at,
        tokens_used = v_window.tokens_used + v_estimated_tokens,
        requests_used = v_window.requests_used + 1,
        quota_day = v_window.quota_day,
        requests_used_today = v_window.requests_used_today + 1
    WHERE queue_key = v_queue_key;

	UPDATE public.llm_request_queue
	SET
	    status = 'PROCESSING',
	    locked_at = now(),
	    locked_by = p_worker_id,
	    lease_expires_at =
	        now() + make_interval(
	            secs => GREATEST(p_lease_seconds, 1)
	        ),
	    attempt_count = attempt_count + 1,
	    last_error = NULL
	WHERE request_id = p_request_id;

    RETURN true;
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
-- Name: sp_cleanup_llm_request_queue(integer, integer, integer, boolean, interval, text, boolean, bigint, bigint, bigint, bigint, bigint); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.sp_cleanup_llm_request_queue(IN p_completed_retention_days integer DEFAULT 7, IN p_failed_retention_days integer DEFAULT 30, IN p_abandoned_retention_days integer DEFAULT 30, IN p_include_orphans boolean DEFAULT false, IN p_orphan_age interval DEFAULT '01:00:00'::interval, IN p_queue_key text DEFAULT NULL::text, IN p_dry_run boolean DEFAULT true, INOUT p_completed_count bigint DEFAULT 0, INOUT p_failed_count bigint DEFAULT 0, INOUT p_abandoned_count bigint DEFAULT 0, INOUT p_orphaned_pending_count bigint DEFAULT 0, INOUT p_expired_processing_count bigint DEFAULT 0)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_lock_acquired boolean;
BEGIN
    IF p_completed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_completed_retention_days must be >= 1';
    END IF;

    IF p_failed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_failed_retention_days must be >= 1';
    END IF;

    IF p_abandoned_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_abandoned_retention_days must be >= 1';
    END IF;

    IF p_orphan_age <= interval '0 seconds' THEN
        RAISE EXCEPTION
            'p_orphan_age must be greater than zero';
    END IF;

    /*
     * Evita due cleanup concorrenti.
     * Il lock viene rilasciato automaticamente al termine
     * della transazione.
     */
    v_lock_acquired := pg_try_advisory_xact_lock(
        hashtext('public.sp_cleanup_llm_request_queue')
    );

    IF NOT v_lock_acquired THEN
        RAISE EXCEPTION
            'Another LLM queue cleanup is already running';
    END IF;

    SELECT count(*)
    INTO p_completed_count
    FROM public.llm_request_queue q
    WHERE q.status = 'COMPLETED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_completed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    SELECT count(*)
    INTO p_failed_count
    FROM public.llm_request_queue q
    WHERE q.status = 'FAILED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_failed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    SELECT count(*)
    INTO p_abandoned_count
    FROM public.llm_request_queue q
    WHERE q.status = 'ABANDONED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_abandoned_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    IF p_include_orphans THEN
        SELECT count(*)
        INTO p_orphaned_pending_count
        FROM public.llm_request_queue q
        WHERE q.status IN ('PENDING', 'RETRY')
          AND q.created_at < now() - p_orphan_age
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );

        SELECT count(*)
        INTO p_expired_processing_count
        FROM public.llm_request_queue q
        WHERE q.status = 'PROCESSING'
          AND q.lease_expires_at IS NOT NULL
          AND q.lease_expires_at < now()
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );
    END IF;

    RAISE NOTICE
        'LLM queue cleanup: completed=%, failed=%, abandoned=%, orphaned_pending=%, expired_processing=%, dry_run=%',
        p_completed_count,
        p_failed_count,
        p_abandoned_count,
        p_orphaned_pending_count,
        p_expired_processing_count,
        p_dry_run;

    IF p_dry_run THEN
        RETURN;
    END IF;

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'COMPLETED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_completed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'FAILED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_failed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'ABANDONED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_abandoned_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    IF p_include_orphans THEN
        /*
         * PENDING e RETRY molto vecchie possono appartenere
         * a processi terminati prima dell'esecuzione.
         */
        DELETE FROM public.llm_request_queue q
        WHERE q.status IN ('PENDING', 'RETRY')
          AND q.created_at < now() - p_orphan_age
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );

        /*
         * Una PROCESSING viene rimossa solo se la lease
         * è valorizzata ed è già scaduta.
         */
        DELETE FROM public.llm_request_queue q
        WHERE q.status = 'PROCESSING'
          AND q.lease_expires_at IS NOT NULL
          AND q.lease_expires_at < now()
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );
    END IF;
END;
$$;


--
-- Name: sp_cleanup_llm_request_queue(integer, integer, integer, boolean, interval, text, boolean, boolean, bigint, bigint, bigint, bigint, bigint, bigint); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.sp_cleanup_llm_request_queue(IN p_completed_retention_days integer DEFAULT 7, IN p_failed_retention_days integer DEFAULT 30, IN p_abandoned_retention_days integer DEFAULT 30, IN p_include_orphans boolean DEFAULT false, IN p_orphan_age interval DEFAULT '01:00:00'::interval, IN p_queue_key text DEFAULT NULL::text, IN p_dry_run boolean DEFAULT true, IN p_purge_all boolean DEFAULT false, INOUT p_completed_count bigint DEFAULT 0, INOUT p_failed_count bigint DEFAULT 0, INOUT p_abandoned_count bigint DEFAULT 0, INOUT p_orphaned_pending_count bigint DEFAULT 0, INOUT p_expired_processing_count bigint DEFAULT 0, INOUT p_purged_count bigint DEFAULT 0)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_lock_acquired boolean;
BEGIN
    IF p_completed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_completed_retention_days must be >= 1';
    END IF;

    IF p_failed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_failed_retention_days must be >= 1';
    END IF;

    IF p_abandoned_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_abandoned_retention_days must be >= 1';
    END IF;

    IF p_orphan_age <= interval '0 seconds' THEN
        RAISE EXCEPTION
            'p_orphan_age must be greater than zero';
    END IF;

    v_lock_acquired := pg_try_advisory_xact_lock(
        hashtext('public.sp_cleanup_llm_request_queue')
    );

    IF NOT v_lock_acquired THEN
        RAISE EXCEPTION
            'Another LLM queue cleanup is already running';
    END IF;

    /*
     * DANGER ZONE
     *
     * La modalità purge elimina ogni richiesta della coda
     * selezionata, inclusi PENDING, RETRY e PROCESSING.
     *
     * Non considera retention, available_at o lease.
     */
    IF p_purge_all THEN
        SELECT count(*)
        INTO p_purged_count
        FROM public.llm_request_queue q
        WHERE (
            p_queue_key IS NULL
            OR q.queue_key = p_queue_key
        );

        RAISE NOTICE
            'LLM queue full purge: rows=%, queue_key=%, dry_run=%',
            p_purged_count,
            coalesce(p_queue_key, '<all>'),
            p_dry_run;

        IF NOT p_dry_run THEN
            DELETE FROM public.llm_request_queue q
            WHERE (
                p_queue_key IS NULL
                OR q.queue_key = p_queue_key
            );
        END IF;

        RETURN;
    END IF;

    SELECT count(*)
    INTO p_completed_count
    FROM public.llm_request_queue q
    WHERE q.status = 'COMPLETED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_completed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    SELECT count(*)
    INTO p_failed_count
    FROM public.llm_request_queue q
    WHERE q.status = 'FAILED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_failed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    SELECT count(*)
    INTO p_abandoned_count
    FROM public.llm_request_queue q
    WHERE q.status = 'ABANDONED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_abandoned_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    IF p_include_orphans THEN
        SELECT count(*)
        INTO p_orphaned_pending_count
        FROM public.llm_request_queue q
        WHERE q.status IN ('PENDING', 'RETRY')
          AND q.created_at < now() - p_orphan_age
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );

        SELECT count(*)
        INTO p_expired_processing_count
        FROM public.llm_request_queue q
        WHERE q.status = 'PROCESSING'
          AND q.lease_expires_at IS NOT NULL
          AND q.lease_expires_at < now()
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );
    END IF;

    RAISE NOTICE
        'LLM queue cleanup: completed=%, failed=%, abandoned=%, orphaned_pending=%, expired_processing=%, dry_run=%',
        p_completed_count,
        p_failed_count,
        p_abandoned_count,
        p_orphaned_pending_count,
        p_expired_processing_count,
        p_dry_run;

    IF p_dry_run THEN
        RETURN;
    END IF;

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'COMPLETED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_completed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'FAILED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_failed_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    DELETE FROM public.llm_request_queue q
    WHERE q.status = 'ABANDONED'
      AND coalesce(q.completed_at, q.created_at)
          < now() - make_interval(
              days => p_abandoned_retention_days
          )
      AND (
          p_queue_key IS NULL
          OR q.queue_key = p_queue_key
      );

    IF p_include_orphans THEN
        DELETE FROM public.llm_request_queue q
        WHERE q.status IN ('PENDING', 'RETRY')
          AND q.created_at < now() - p_orphan_age
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );

        DELETE FROM public.llm_request_queue q
        WHERE q.status = 'PROCESSING'
          AND q.lease_expires_at IS NOT NULL
          AND q.lease_expires_at < now()
          AND (
              p_queue_key IS NULL
              OR q.queue_key = p_queue_key
          );
    END IF;
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
-- Name: sp_cleanup_orphan_entity(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_cleanup_orphan_entity(p_entity_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM 1
    FROM public.entities e
    WHERE e.entity_id = p_entity_id
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN 'skipped';
    END IF;

    DELETE FROM public.entities e
    WHERE e.entity_id = p_entity_id
      AND NOT EXISTS (
          SELECT 1 FROM public.instruments i WHERE i.entity_id = e.entity_id
      );

    IF FOUND THEN
        RETURN 'deleted';
    END IF;
    RETURN 'skipped';
EXCEPTION
    WHEN foreign_key_violation THEN
        RETURN 'referenced';
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
-- Name: sp_consolidate_entity(uuid, uuid, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_consolidate_entity(p_source_entity_id uuid, p_target_entity_id uuid, p_force boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source entities%ROWTYPE;
    v_target entities%ROWTYPE;
    v_remaining_instruments integer;
BEGIN
    IF p_source_entity_id IS NULL
       OR p_target_entity_id IS NULL
    THEN
        RAISE EXCEPTION
            'Source and target entity IDs are required';
    END IF;

    IF p_source_entity_id = p_target_entity_id THEN
        RETURN jsonb_build_object(
            'status', 'UNCHANGED',
            'entity_id', p_target_entity_id
        );
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            LEAST(
                p_source_entity_id::text,
                p_target_entity_id::text
            ),
            0
        )
    );

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            GREATEST(
                p_source_entity_id::text,
                p_target_entity_id::text
            ),
            0
        )
    );

    SELECT *
    INTO STRICT v_source
    FROM entities
    WHERE entity_id = p_source_entity_id
    FOR UPDATE;

    SELECT *
    INTO STRICT v_target
    FROM entities
    WHERE entity_id = p_target_entity_id
    FOR UPDATE;

    IF NOT p_force
       AND v_source.entity_type IS DISTINCT FROM v_target.entity_type
    THEN
        RAISE EXCEPTION
            'Entity type mismatch: source %, target %',
            v_source.entity_type,
            v_target.entity_type;
    END IF;

    IF NOT p_force
       AND v_source.country_code IS NOT NULL
       AND v_target.country_code IS NOT NULL
       AND upper(trim(v_source.country_code))
           IS DISTINCT FROM upper(trim(v_target.country_code))
    THEN
        RAISE EXCEPTION
            'Entity country mismatch: source %, target %',
            v_source.country_code,
            v_target.country_code;
    END IF;

    IF NOT p_force
       AND sp_normalize_entity_name(v_source.name)
           IS DISTINCT FROM sp_normalize_entity_name(v_target.name)
    THEN
        RAISE EXCEPTION
            'Normalized entity name mismatch: source "%", target "%"',
            sp_normalize_entity_name(v_source.name),
            sp_normalize_entity_name(v_target.name);
    END IF;

    /*
     * Preserve the source name as an alias.
     */
    INSERT INTO entity_aliases (
        entity_id,
        alias_name
    )
    VALUES (
        p_target_entity_id,
        v_source.name
    )
    ON CONFLICT DO NOTHING;

    INSERT INTO entity_aliases (
        entity_id,
        alias_name
    )
    SELECT
        p_target_entity_id,
        alias_name
    FROM entity_aliases
    WHERE entity_id = p_source_entity_id
    ON CONFLICT DO NOTHING;

    DELETE FROM entity_aliases
    WHERE entity_id = p_source_entity_id;

    /*
     * Entity identities.
     */
    INSERT INTO entity_identities (
        entity_id,
        identity_id,
        is_primary,
        verified_at,
        created_at
    )
    SELECT
        p_target_entity_id,
        identity_id,
        is_primary,
        verified_at,
        created_at
    FROM entity_identities
    WHERE entity_id = p_source_entity_id
    ON CONFLICT (entity_id, identity_id)
    DO UPDATE SET
        is_primary =
            entity_identities.is_primary
            OR EXCLUDED.is_primary,
        verified_at = COALESCE(
            entity_identities.verified_at,
            EXCLUDED.verified_at
        );

    DELETE FROM entity_identities
    WHERE entity_id = p_source_entity_id;

    /*
     * Distinct listings are reparented, not deleted.
     */
    UPDATE instruments
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    /*
     * Watchlists have UNIQUE(watchlist_id, entity_id).
     */
    UPDATE watchlist_items source
    SET entity_id = p_target_entity_id
    WHERE source.entity_id = p_source_entity_id
      AND NOT EXISTS (
          SELECT 1
          FROM watchlist_items target
          WHERE target.watchlist_id = source.watchlist_id
            AND target.entity_id = p_target_entity_id
      );

    UPDATE watchlist_items target
    SET
        priority = COALESCE(target.priority, source.priority),
        target_price = COALESCE(
            target.target_price,
            source.target_price
        ),
        notes = CASE
            WHEN NULLIF(trim(target.notes), '') IS NULL
                THEN source.notes
            WHEN NULLIF(trim(source.notes), '') IS NULL
                THEN target.notes
            WHEN target.notes = source.notes
                THEN target.notes
            ELSE target.notes || E'\n' || source.notes
        END
    FROM watchlist_items source
    WHERE source.entity_id = p_source_entity_id
      AND target.watchlist_id = source.watchlist_id
      AND target.entity_id = p_target_entity_id;

    DELETE FROM watchlist_items
    WHERE entity_id = p_source_entity_id;

    /*
     * Tables where duplicate rows can be dropped safely after transferring
     * their information.
     */
    INSERT INTO entity_sectors (
        entity_id,
        sector_id,
        weight
    )
    SELECT
        p_target_entity_id,
        sector_id,
        weight
    FROM entity_sectors
    WHERE entity_id = p_source_entity_id
    ON CONFLICT (entity_id, sector_id)
    DO UPDATE SET
        weight = GREATEST(
            COALESCE(entity_sectors.weight, 0),
            COALESCE(EXCLUDED.weight, 0)
        );

    DELETE FROM entity_sectors
    WHERE entity_id = p_source_entity_id;

    INSERT INTO entity_tags (
        entity_id,
        tag_id
    )
    SELECT
        p_target_entity_id,
        tag_id
    FROM entity_tags
    WHERE entity_id = p_source_entity_id
    ON CONFLICT (entity_id, tag_id) DO NOTHING;

    DELETE FROM entity_tags
    WHERE entity_id = p_source_entity_id;

    INSERT INTO entity_metrics (
        entity_id,
        metric_name,
        metric_value,
        metric_date
    )
    SELECT
        p_target_entity_id,
        metric_name,
        metric_value,
        metric_date
    FROM entity_metrics
    WHERE entity_id = p_source_entity_id
    ON CONFLICT (
        entity_id,
        metric_name,
        metric_date
    )
    DO UPDATE SET
        metric_value = COALESCE(
            EXCLUDED.metric_value,
            entity_metrics.metric_value
        );

    DELETE FROM entity_metrics
    WHERE entity_id = p_source_entity_id;

    /*
     * Simple entity references.
     */
    UPDATE catalysts
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id
      AND NOT EXISTS (
          SELECT 1
          FROM catalysts target
          WHERE target.entity_id = p_target_entity_id
            AND target.catalyst_type = catalysts.catalyst_type
            AND target.catalyst_date
                IS NOT DISTINCT FROM catalysts.catalyst_date
      );

    DELETE FROM catalysts source
    WHERE source.entity_id = p_source_entity_id
      AND EXISTS (
          SELECT 1
          FROM catalysts target
          WHERE target.entity_id = p_target_entity_id
            AND target.catalyst_type = source.catalyst_type
            AND target.catalyst_date
                IS NOT DISTINCT FROM source.catalyst_date
      );

    UPDATE filings
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE etf_constituents
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE instrument_entity_exposure
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE news_entities source
    SET entity_id = p_target_entity_id
    WHERE source.entity_id = p_source_entity_id
      AND NOT EXISTS (
          SELECT 1
          FROM news_entities target
          WHERE target.news_id = source.news_id
            AND target.entity_id = p_target_entity_id
      );

    DELETE FROM news_entities
    WHERE entity_id = p_source_entity_id;

    UPDATE news_impacts source
    SET entity_id = p_target_entity_id
    WHERE source.entity_id = p_source_entity_id
      AND NOT EXISTS (
          SELECT 1
          FROM news_impacts target
          WHERE target.news_id = source.news_id
            AND target.entity_id = p_target_entity_id
      );

    DELETE FROM news_impacts
    WHERE entity_id = p_source_entity_id;

    UPDATE notifications
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE notification_hashes
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE insider_transactions
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE signal_factors
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    INSERT INTO signal_history (
        entity_id,
        overall_score,
        signal,
        trend,
        confidence,
        reason,
        created_at
    )
    SELECT
        p_target_entity_id,
        overall_score,
        signal,
        trend,
        confidence,
        reason,
        created_at
    FROM signal_history
    WHERE entity_id = p_source_entity_id;

    DELETE FROM signal_history
    WHERE entity_id = p_source_entity_id;

    INSERT INTO signals (
        entity_id,
        overall_score,
        signal,
        trend,
        confidence,
        last_reason
    )
    SELECT
        p_target_entity_id,
        overall_score,
        signal,
        trend,
        confidence,
        last_reason
    FROM signals
    WHERE entity_id = p_source_entity_id
    ON CONFLICT (entity_id)
    DO NOTHING;

    DELETE FROM signals
    WHERE entity_id = p_source_entity_id;

    UPDATE portfolio_impacts
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    UPDATE entity_impact_history
    SET entity_id = p_target_entity_id
    WHERE entity_id = p_source_entity_id;

    /*
     * Cache rows are derived. Preserve the target and discard conflicting
     * source rows, so they can be recalculated.
     */
    UPDATE cache_portfolio_exposure source
    SET entity_id = p_target_entity_id
    WHERE source.entity_id = p_source_entity_id
      AND NOT EXISTS (
          SELECT 1
          FROM cache_portfolio_exposure target
          WHERE target.portfolio_id = source.portfolio_id
            AND target.entity_id = p_target_entity_id
      );

    DELETE FROM cache_portfolio_exposure
    WHERE entity_id = p_source_entity_id;

    /*
     * Entity relationships.
     */
    UPDATE entity_relationships
    SET source_entity_id = p_target_entity_id
    WHERE source_entity_id = p_source_entity_id;

    UPDATE entity_relationships
    SET target_entity_id = p_target_entity_id
    WHERE target_entity_id = p_source_entity_id;

    DELETE FROM entity_relationships
    WHERE source_entity_id = target_entity_id;

    /*
     * Prefer target metadata, fill only missing fields.
     */
    UPDATE entities
    SET
        country_code = COALESCE(
            entities.country_code,
            v_source.country_code
        ),
        description = COALESCE(
            entities.description,
            v_source.description
        ),
        active = entities.active OR v_source.active
    WHERE entity_id = p_target_entity_id;

    SELECT count(*)
    INTO v_remaining_instruments
    FROM instruments
    WHERE entity_id = p_source_entity_id;

    IF v_remaining_instruments <> 0 THEN
        RAISE EXCEPTION
            'Source entity still owns % instruments',
            v_remaining_instruments;
    END IF;

    DELETE FROM entities
    WHERE entity_id = p_source_entity_id;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'source_entity_id', p_source_entity_id,
        'target_entity_id', p_target_entity_id,
        'source_name', v_source.name,
        'target_name', v_target.name
    );
EXCEPTION
    WHEN no_data_found THEN
        RAISE EXCEPTION
            'Source or target entity was not found';
END;
$$;


--
-- Name: sp_consolidate_instrument(uuid, uuid, boolean, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_consolidate_instrument(p_source_instrument_id uuid, p_target_instrument_id uuid, p_consolidate_entity boolean DEFAULT true, p_force boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source instruments%ROWTYPE;
    v_target instruments%ROWTYPE;

    v_source_entity entities%ROWTYPE;
    v_target_entity entities%ROWTYPE;

    v_position record;
    v_target_position positions%ROWTYPE;

    v_source_quantity numeric;
    v_target_quantity numeric;
    v_combined_quantity numeric;
    v_combined_avg_price numeric;

    v_source_queue market_price_refresh_queue%ROWTYPE;
    v_target_queue market_price_refresh_queue%ROWTYPE;

    v_moved_positions integer := 0;
    v_merged_positions integer := 0;
    v_moved_prices integer := 0;
    v_moved_snapshots integer := 0;
    v_moved_identities integer := 0;
    v_source_entity_deleted boolean := false;
BEGIN
    IF p_source_instrument_id IS NULL
       OR p_target_instrument_id IS NULL
    THEN
        RAISE EXCEPTION
            'Source and target instrument IDs are required';
    END IF;

    IF p_source_instrument_id = p_target_instrument_id THEN
        RAISE EXCEPTION
            'Source and target instruments must be different';
    END IF;

    /*
     * Serialize consolidations concerning either instrument.
     * The lower/higher ordering avoids lock inversion.
     */
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            LEAST(
                p_source_instrument_id::text,
                p_target_instrument_id::text
            ),
            0
        )
    );

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            GREATEST(
                p_source_instrument_id::text,
                p_target_instrument_id::text
            ),
            0
        )
    );

    SELECT *
    INTO STRICT v_source
    FROM instruments
    WHERE instrument_id = p_source_instrument_id
    FOR UPDATE;

    SELECT *
    INTO STRICT v_target
    FROM instruments
    WHERE instrument_id = p_target_instrument_id
    FOR UPDATE;

    SELECT *
    INTO STRICT v_source_entity
    FROM entities
    WHERE entity_id = v_source.entity_id
    FOR UPDATE;

    SELECT *
    INTO STRICT v_target_entity
    FROM entities
    WHERE entity_id = v_target.entity_id
    FOR UPDATE;

    /*
     * Safe defaults. "US" and NULL exchange are considered compatible
     * only when the symbol is the same and p_force is not required.
     */
    IF NOT p_force
       AND upper(trim(v_source.symbol))
           IS DISTINCT FROM upper(trim(v_target.symbol))
    THEN
        RAISE EXCEPTION
            'Symbol mismatch: source %, target %',
            v_source.symbol,
            v_target.symbol;
    END IF;

    IF NOT p_force
       AND v_source.instrument_type IS NOT NULL
       AND v_target.instrument_type IS NOT NULL
       AND upper(trim(v_source.instrument_type))
           IS DISTINCT FROM upper(trim(v_target.instrument_type))
    THEN
        RAISE EXCEPTION
            'Instrument type mismatch: source %, target %',
            v_source.instrument_type,
            v_target.instrument_type;
    END IF;

    IF NOT p_force
       AND v_source.currency_code IS NOT NULL
       AND v_target.currency_code IS NOT NULL
       AND upper(trim(v_source.currency_code))
           IS DISTINCT FROM upper(trim(v_target.currency_code))
    THEN
        RAISE EXCEPTION
            'Currency mismatch: source %, target %',
            v_source.currency_code,
            v_target.currency_code;
    END IF;

    /*
     * An identity may be shared by multiple listings of the same entity,
     * therefore only the relation is copied.
     */
    INSERT INTO instrument_identities (
        instrument_id,
        identity_id,
        is_primary,
        verified_at,
        created_at
    )
    SELECT
        p_target_instrument_id,
        ii.identity_id,
        ii.is_primary,
        ii.verified_at,
        ii.created_at
    FROM instrument_identities ii
    WHERE ii.instrument_id = p_source_instrument_id
    ON CONFLICT (instrument_id, identity_id)
    DO UPDATE SET
        is_primary =
            instrument_identities.is_primary
            OR EXCLUDED.is_primary,
        verified_at = COALESCE(
            instrument_identities.verified_at,
            EXCLUDED.verified_at
        );

    GET DIAGNOSTICS v_moved_identities = ROW_COUNT;

    DELETE FROM instrument_identities
    WHERE instrument_id = p_source_instrument_id;

    /*
     * Positions have UNIQUE(portfolio_id, instrument_id).
     * If both instruments occur in the same portfolio, merge quantity
     * and calculate a weighted average purchase price.
     */
    FOR v_position IN
        SELECT *
        FROM positions
        WHERE instrument_id = p_source_instrument_id
        FOR UPDATE
    LOOP
        SELECT *
        INTO v_target_position
        FROM positions
        WHERE portfolio_id = v_position.portfolio_id
          AND instrument_id = p_target_instrument_id
        FOR UPDATE;

        IF FOUND THEN
            v_source_quantity := COALESCE(v_position.quantity, 0);
            v_target_quantity := COALESCE(v_target_position.quantity, 0);
            v_combined_quantity :=
                v_source_quantity + v_target_quantity;

            v_combined_avg_price :=
                CASE
                    WHEN v_combined_quantity = 0 THEN
                        COALESCE(
                            v_target_position.avg_price,
                            v_position.avg_price
                        )
                    WHEN v_target_position.avg_price IS NULL THEN
                        v_position.avg_price
                    WHEN v_position.avg_price IS NULL THEN
                        v_target_position.avg_price
                    ELSE
                        (
                            v_target_quantity
                            * v_target_position.avg_price
                            + v_source_quantity
                            * v_position.avg_price
                        )
                        / v_combined_quantity
                END;

            UPDATE positions
            SET
                quantity = v_combined_quantity,
                avg_price = v_combined_avg_price,
                opened_at = CASE
                    WHEN opened_at IS NULL THEN v_position.opened_at
                    WHEN v_position.opened_at IS NULL THEN opened_at
                    ELSE LEAST(opened_at, v_position.opened_at)
                END,
                notes = CASE
                    WHEN NULLIF(trim(notes), '') IS NULL
                        THEN v_position.notes
                    WHEN NULLIF(trim(v_position.notes), '') IS NULL
                        THEN notes
                    WHEN notes = v_position.notes
                        THEN notes
                    ELSE notes || E'\n' || v_position.notes
                END
            WHERE position_id = v_target_position.position_id;

            DELETE FROM positions
            WHERE position_id = v_position.position_id;

            v_merged_positions := v_merged_positions + 1;
        ELSE
            UPDATE positions
            SET instrument_id = p_target_instrument_id
            WHERE position_id = v_position.position_id;

            v_moved_positions := v_moved_positions + 1;
        END IF;
    END LOOP;

    /*
     * Historical prices. On a timestamp collision the target wins,
     * following the same authoritative-target principle used by Ghostfolio.
     */
    INSERT INTO price_history (
        instrument_id,
        ts,
        open,
        high,
        low,
        close,
        volume
    )
    SELECT
        p_target_instrument_id,
        source.ts,
        source.open,
        source.high,
        source.low,
        source.close,
        source.volume
    FROM price_history source
    WHERE source.instrument_id = p_source_instrument_id
      AND NOT EXISTS (
          SELECT 1
          FROM price_history target
          WHERE target.instrument_id = p_target_instrument_id
            AND target.ts IS NOT DISTINCT FROM source.ts
      );

    GET DIAGNOSTICS v_moved_prices = ROW_COUNT;

    DELETE FROM price_history
    WHERE instrument_id = p_source_instrument_id;

    /*
     * Portfolio holding snapshots:
     * preserve one row per portfolio/instrument/date.
     */
    INSERT INTO portfolio_holdings_snapshots (
        portfolio_id,
        instrument_id,
        snapshot_date,
        quantity,
        market_value,
        weight,
        created_at
    )
    SELECT
        source.portfolio_id,
        p_target_instrument_id,
        source.snapshot_date,
        source.quantity,
        source.market_value,
        source.weight,
        source.created_at
    FROM portfolio_holdings_snapshots source
    WHERE source.instrument_id = p_source_instrument_id
    ON CONFLICT (
        portfolio_id,
        instrument_id,
        snapshot_date
    )
    DO UPDATE SET
        quantity = COALESCE(
            portfolio_holdings_snapshots.quantity,
            0
        ) + COALESCE(EXCLUDED.quantity, 0),
        market_value = COALESCE(
            portfolio_holdings_snapshots.market_value,
            0
        ) + COALESCE(EXCLUDED.market_value, 0),
        weight = COALESCE(
            portfolio_holdings_snapshots.weight,
            EXCLUDED.weight
        );

    GET DIAGNOSTICS v_moved_snapshots = ROW_COUNT;

    DELETE FROM portfolio_holdings_snapshots
    WHERE instrument_id = p_source_instrument_id;

    /*
     * ETF constituents where this instrument is the ETF.
     */
    UPDATE etf_constituents
    SET etf_instrument_id = p_target_instrument_id
    WHERE etf_instrument_id = p_source_instrument_id;

    /*
     * Exposure mappings.
     */
    UPDATE instrument_entity_exposure source
    SET instrument_id = p_target_instrument_id
    WHERE source.instrument_id = p_source_instrument_id
      AND NOT EXISTS (
          SELECT 1
          FROM instrument_entity_exposure target
          WHERE target.instrument_id = p_target_instrument_id
            AND target.entity_id = source.entity_id
            AND target.effective_date
                IS NOT DISTINCT FROM source.effective_date
      );

    DELETE FROM instrument_entity_exposure
    WHERE instrument_id = p_source_instrument_id;

    /*
     * Parent/child relations. Delete self-relations and duplicate edges.
     */
    UPDATE instrument_relations
    SET parent_instrument_id = p_target_instrument_id
    WHERE parent_instrument_id = p_source_instrument_id;

    UPDATE instrument_relations
    SET child_instrument_id = p_target_instrument_id
    WHERE child_instrument_id = p_source_instrument_id;

    DELETE FROM instrument_relations
    WHERE parent_instrument_id = child_instrument_id;

    DELETE FROM instrument_relations duplicate
    USING instrument_relations canonical
    WHERE duplicate.relation_id <> canonical.relation_id
      AND duplicate.parent_instrument_id =
          canonical.parent_instrument_id
      AND duplicate.child_instrument_id =
          canonical.child_instrument_id
      AND duplicate.relation_type
          IS NOT DISTINCT FROM canonical.relation_type
      AND duplicate.relation_id > canonical.relation_id;

    /*
     * Price refresh queue has UNIQUE(instrument_id).
     */
    SELECT *
    INTO v_source_queue
    FROM market_price_refresh_queue
    WHERE instrument_id = p_source_instrument_id
    FOR UPDATE;

    IF FOUND THEN
        SELECT *
        INTO v_target_queue
        FROM market_price_refresh_queue
        WHERE instrument_id = p_target_instrument_id
        FOR UPDATE;

        IF FOUND THEN
            UPDATE market_price_refresh_queue
            SET
                status = CASE
                    WHEN v_target_queue.status = 'PROCESSING'
                        THEN 'PROCESSING'
                    WHEN v_source_queue.status IN ('PENDING', 'RETRY')
                        THEN 'PENDING'
                    ELSE v_target_queue.status
                END,
                next_attempt_at = LEAST(
                    v_target_queue.next_attempt_at,
                    v_source_queue.next_attempt_at
                ),
                last_error = COALESCE(
                    v_target_queue.last_error,
                    v_source_queue.last_error
                ),
                updated_at = now()
            WHERE instrument_id = p_target_instrument_id;

            DELETE FROM market_price_refresh_queue
            WHERE instrument_id = p_source_instrument_id;
        ELSE
            UPDATE market_price_refresh_queue
            SET instrument_id = p_target_instrument_id
            WHERE instrument_id = p_source_instrument_id;
        END IF;
    END IF;

    /*
     * Preserve aliases. The source symbol is useful when providers use
     * alternative representations.
     */
    INSERT INTO instrument_aliases (
        instrument_id,
        alias_symbol
    )
    SELECT
        p_target_instrument_id,
        alias_symbol
    FROM instrument_aliases
    WHERE instrument_id = p_source_instrument_id
    ON CONFLICT (instrument_id, upper(btrim((alias_symbol)::text))) WHERE ((instrument_id IS NOT NULL) AND (NULLIF(btrim((alias_symbol)::text), ''::text) IS NOT NULL)) DO NOTHING;

    IF v_source.symbol IS DISTINCT FROM v_target.symbol THEN
        INSERT INTO instrument_aliases (
            instrument_id,
            alias_symbol
        )
        VALUES (
            p_target_instrument_id,
            v_source.symbol
        )
        ON CONFLICT (instrument_id, upper(btrim((alias_symbol)::text))) WHERE ((instrument_id IS NOT NULL) AND (NULLIF(btrim((alias_symbol)::text), ''::text) IS NOT NULL)) DO NOTHING;
    END IF;

    DELETE FROM instrument_aliases
    WHERE instrument_id = p_source_instrument_id;



    /*
     * Record the operation before removing the source.
     */
    INSERT INTO instrument_consolidation_audit (
        duplicate_instrument_id,
        canonical_instrument_id,
        duplicate_entity_id,
        canonical_entity_id,
        symbol,
        exchange,
        details
    )
    VALUES (
        p_source_instrument_id,
        p_target_instrument_id,
        v_source.entity_id,
        v_target.entity_id,
        v_source.symbol,
        v_source.exchange,
        jsonb_build_object(
            'source_name', v_source_entity.name,
            'target_name', v_target_entity.name,
            'source_data_source', v_source.data_source,
            'target_data_source', v_target.data_source,
            'source_currency', v_source.currency_code,
            'target_currency', v_target.currency_code,
            'moved_positions', v_moved_positions,
            'merged_positions', v_merged_positions,
            'moved_prices', v_moved_prices,
            'moved_snapshots', v_moved_snapshots,
            'moved_identities', v_moved_identities,
            'forced', p_force
        )
    )
    ON CONFLICT (duplicate_instrument_id)
    DO NOTHING;

    DELETE FROM instruments
    WHERE instrument_id = p_source_instrument_id;

    /*
     * Fill only missing target metadata. Existing target metadata remains
     * authoritative.
     */
    UPDATE instruments
    SET
        exchange = COALESCE(
            instruments.exchange,
            v_source.exchange
        ),
        instrument_type = COALESCE(
            instruments.instrument_type,
            v_source.instrument_type
        ),
        currency_code = COALESCE(
            instruments.currency_code,
            v_source.currency_code
        ),
        data_source = COALESCE(
            instruments.data_source,
            v_source.data_source
        ),
        last_price = CASE
            WHEN instruments.price_as_of IS NULL
                THEN COALESCE(
                    instruments.last_price,
                    v_source.last_price
                )
            WHEN v_source.price_as_of IS NOT NULL
             AND v_source.price_as_of > instruments.price_as_of
                THEN v_source.last_price
            ELSE instruments.last_price
        END,
        price_source_id = CASE
            WHEN instruments.price_as_of IS NULL
                THEN COALESCE(
                    instruments.price_source_id,
                    v_source.price_source_id
                )
            WHEN v_source.price_as_of IS NOT NULL
             AND v_source.price_as_of > instruments.price_as_of
                THEN v_source.price_source_id
            ELSE instruments.price_source_id
        END,
        price_as_of = CASE
            WHEN v_source.price_as_of IS NULL
                THEN instruments.price_as_of
            WHEN instruments.price_as_of IS NULL
                THEN v_source.price_as_of
            ELSE GREATEST(
                instruments.price_as_of,
                v_source.price_as_of
            )
        END
    WHERE instrument_id = p_target_instrument_id;

    /*
     * If requested, merge the now orphaned source entity into the entity
     * owning the target instrument.
     */
    IF p_consolidate_entity
       AND v_source.entity_id <> v_target.entity_id
    THEN
        PERFORM sp_consolidate_entity(
            v_source.entity_id,
            v_target.entity_id,
            p_force
        );

        v_source_entity_deleted := NOT EXISTS (
            SELECT 1
            FROM entities
            WHERE entity_id = v_source.entity_id
        );
    END IF;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'source_instrument_id', p_source_instrument_id,
        'target_instrument_id', p_target_instrument_id,
        'source_entity_id', v_source.entity_id,
        'target_entity_id', v_target.entity_id,
        'moved_positions', v_moved_positions,
        'merged_positions', v_merged_positions,
        'moved_prices', v_moved_prices,
        'moved_snapshots', v_moved_snapshots,
        'moved_identities', v_moved_identities,
        'source_entity_deleted', v_source_entity_deleted
    );
EXCEPTION
    WHEN no_data_found THEN
        RAISE EXCEPTION
            'Source or target instrument/entity was not found';
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
-- Name: sp_delete_user_notification_source(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_delete_user_notification_source(p_user_id uuid, p_source_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.notification_subscriptions ns
        WHERE ns.user_id = p_user_id
          AND ns.source_id = p_source_id
          AND ns.enabled = true
    ) THEN
        RAISE EXCEPTION 'Notification source is used by an enabled preference';
    END IF;

    DELETE FROM public.user_sources us
    WHERE us.user_id = p_user_id
      AND us.source_id = p_source_id;
    RETURN FOUND;
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
-- Name: sp_enqueue_market_price_refresh(uuid, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_enqueue_market_price_refresh(p_instrument_id uuid, p_symbol character varying, p_data_source character varying) RETURNS uuid
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

CREATE FUNCTION public.sp_get_entities_for_refresh(p_mode character varying DEFAULT 'MISSING'::character varying, p_symbol character varying DEFAULT NULL::character varying) RETURNS TABLE(entity_id uuid, instrument_id uuid, symbol character varying, exchange character varying, currency_code character, instrument_type character varying)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF upper(p_mode) NOT IN ('MISSING', 'ALL', 'SYMBOL') THEN
        RAISE EXCEPTION 'Invalid refresh mode: %', p_mode;
    END IF;
    IF upper(p_mode) = 'SYMBOL' AND nullif(btrim(p_symbol), '') IS NULL THEN
        RAISE EXCEPTION 'p_symbol is required when p_mode is SYMBOL';
    END IF;

    RETURN QUERY
    SELECT
        e.entity_id,
        i.instrument_id,
        i.symbol,
        i.exchange,
        i.currency_code,
        i.instrument_type
    FROM public.entities e
    JOIN public.instruments i ON i.entity_id = e.entity_id
    WHERE e.active = true
      AND (
          (upper(p_mode) = 'MISSING' AND (
              upper(e.name) = upper(i.symbol)
              OR e.country_code IS NULL
              OR i.currency_code IS NULL
              OR i.exchange IS NULL
          ))
          OR upper(p_mode) = 'ALL'
          OR (upper(p_mode) = 'SYMBOL'
              AND upper(i.symbol) = upper(btrim(p_symbol)))
      )
    ORDER BY i.symbol, i.instrument_id;
END;
$$;


--
-- Name: sp_get_entity_performance(uuid, uuid, date, date, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_entity_performance(p_entity_id uuid, p_user_id uuid, p_date_from date, p_date_to date, p_reporting_currency character DEFAULT NULL::bpchar) RETURNS TABLE(entity_id uuid, instrument_id uuid, symbol character varying, exchange character varying, currency_code character, isin character varying, price_date date, native_close numeric, reporting_currency character, reporting_close numeric, native_return_pct numeric, reporting_return_pct numeric)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_reporting_currency char(3);
BEGIN
    IF p_entity_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'p_entity_id and p_user_id are required';
    END IF;

    IF p_date_from IS NULL OR p_date_to IS NULL OR p_date_from > p_date_to THEN
        RAISE EXCEPTION 'Invalid date range: % - %', p_date_from, p_date_to;
    END IF;

    SELECT upper(btrim(coalesce(p_reporting_currency, u.reporting_currency)))
    INTO v_reporting_currency
    FROM public.users u
    WHERE u.user_id = p_user_id
      AND u.is_active = true;

    IF v_reporting_currency IS NULL THEN
        RAISE EXCEPTION 'Active user not found or reporting currency unavailable: %',
            p_user_id;
    END IF;

    RETURN QUERY
    WITH instrument_rows AS (
        SELECT
            i.entity_id,
            i.instrument_id,
            i.symbol,
            i.exchange,
            i.currency_code,
            (
                SELECT id.identity_value
                FROM public.instrument_identities ii
                JOIN public.identities id
                  ON id.identity_id = ii.identity_id
                JOIN public.identity_types it
                  ON it.identity_type_id = id.identity_type_id
                WHERE ii.instrument_id = i.instrument_id
                  AND it.target_kind = 'INSTRUMENT'
                  AND upper(it.code) = 'ISIN'
                ORDER BY
                    ii.is_primary DESC,
                    ii.verified_at DESC NULLS LAST,
                    ii.created_at DESC,
                    id.identity_id
                LIMIT 1
            ) AS isin
        FROM public.instruments i
        JOIN public.entities e ON e.entity_id = i.entity_id
        WHERE i.entity_id = p_entity_id
          AND e.active = true
          AND nullif(btrim(i.currency_code), '') IS NOT NULL
    ), prices AS (
        SELECT
            ir.*,
            ph.ts::date AS price_date,
            ph.close AS native_close,
            public.fx_convert_at_date(
                ph.close,
                ir.currency_code,
                v_reporting_currency,
                ph.ts::date
            ) AS reporting_close
        FROM instrument_rows ir
        JOIN public.price_history ph
          ON ph.instrument_id = ir.instrument_id
        WHERE ph.ts::date BETWEEN p_date_from AND p_date_to
          AND ph.close IS NOT NULL
    ), based AS (
        SELECT
            p.*,
            first_value(p.native_close) OVER (
                PARTITION BY p.instrument_id
                ORDER BY p.price_date
            ) AS native_base,
            first_value(p.reporting_close) OVER (
                PARTITION BY p.instrument_id
                ORDER BY p.price_date
            ) AS reporting_base
        FROM prices p
    )
    SELECT
        b.entity_id,
        b.instrument_id,
        b.symbol,
        b.exchange,
        b.currency_code,
        b.isin,
        b.price_date,
        b.native_close,
        v_reporting_currency,
        b.reporting_close,
        CASE WHEN b.native_base <> 0
             THEN ((b.native_close / b.native_base) - 1) * 100 END,
        CASE WHEN b.reporting_base <> 0
             THEN ((b.reporting_close / b.reporting_base) - 1) * 100 END
    FROM based b
    ORDER BY b.isin NULLS LAST, b.symbol, b.exchange NULLS LAST, b.price_date;
END;
$$;


--
-- Name: sp_get_entity_symbols(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_entity_symbols(p_entity_id uuid) RETURNS TABLE(instrument_id uuid, symbol character varying, exchange character varying, currency_code character, is_source_symbol boolean)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        i.instrument_id,
        i.symbol,
        i.exchange,
        i.currency_code,
        false AS is_source_symbol
    FROM public.instruments i
    WHERE i.entity_id = p_entity_id
      AND NULLIF(btrim(i.symbol), '') IS NOT NULL
    ORDER BY
        i.symbol,
        i.exchange NULLS LAST,
        i.instrument_id;
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


--
-- Name: sp_get_intelligence_entity_candidates(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_intelligence_entity_candidates() RETURNS TABLE(user_id uuid, entity_id uuid, instrument_id uuid, symbol character varying, name character varying, exchange character varying, currency_code character, instrument_type character varying, quantity numeric, market_value numeric, allocation_pct numeric, in_portfolio boolean, in_watchlist boolean, related_symbols jsonb)
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
-- Name: sp_get_portfolio_pricing_states(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_portfolio_pricing_states(p_user_id uuid, p_portfolio_id uuid DEFAULT NULL::uuid) RETURNS TABLE(portfolio_id uuid, total_position_count bigint, pending_price_count bigint, price_refresh_pending boolean)
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


--
-- Name: sp_get_portfolio_summary(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_portfolio_summary(p_portfolio_id uuid, p_user_id uuid) RETURNS TABLE(portfolio_currency character, reporting_currency character, invested_value_native numeric, portfolio_value_native numeric, profit_loss_native numeric, invested_value_reporting numeric, portfolio_value_reporting numeric, profit_loss_reporting numeric, profit_loss_pct numeric, positions_count bigint)
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


--
-- Name: sp_get_portfolios(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_portfolios(p_user_id uuid) RETURNS TABLE(portfolio_id uuid, user_id uuid, name character varying, portfolio_type character varying, description text, base_currency character, invested_value numeric, portfolio_value numeric, profit_loss numeric, profit_loss_pct numeric, positions_count bigint, created_at timestamp with time zone, updated_at timestamp with time zone)
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
-- Name: sp_get_positions_with_pricing(uuid, uuid, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_positions_with_pricing(p_portfolio_id uuid, p_user_id uuid, p_max_age_hours numeric DEFAULT 96) RETURNS SETOF jsonb
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


--
-- Name: sp_get_recent_catalysts(uuid[], integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_recent_catalysts(p_entity_ids uuid[], p_days integer DEFAULT 30) RETURNS TABLE(catalyst_id uuid, entity_id uuid, catalyst_type character varying, catalyst_date timestamp with time zone, status character varying, importance numeric, title text, source character varying, raw_payload jsonb, symbols jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        c.catalyst_id,
        c.entity_id,
        c.catalyst_type,
        c.catalyst_date,
        c.status,
        c.importance,
        c.title,
        c.source,
        c.raw_payload,
        COALESCE(
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
                WHERE i.entity_id = c.entity_id
            ),
            '[]'::jsonb
        ) AS symbols
    FROM public.catalysts c
    WHERE c.entity_id = ANY(p_entity_ids)
      AND c.catalyst_date >=
          now() - make_interval(days => GREATEST(p_days, 0))
    ORDER BY
        c.catalyst_date DESC,
        c.catalyst_id;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

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
-- Name: sp_get_scheduler_job(character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_scheduler_job(p_job_key character varying) RETURNS SETOF public.scheduler_jobs
    LANGUAGE sql STABLE
    AS $$
    SELECT sj.*
    FROM scheduler_jobs sj
    WHERE sj.job_key = p_job_key;
$$;


--
-- Name: sp_get_symbol(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_symbol(p_symbol character varying, p_cache_hours integer DEFAULT 168) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_symbol varchar;
    v_local jsonb;
    v_cache jsonb;
    v_cache_item jsonb;
BEGIN
    v_symbol := UPPER(NULLIF(BTRIM(p_symbol), ''));

    IF v_symbol IS NULL THEN
        RETURN jsonb_build_object(
            'found', false,
            'source', 'MISS',
            'payload', NULL
        );
    END IF;

    ---------------------------------------------------------------------------
    -- LOCAL
    ---------------------------------------------------------------------------

    SELECT jsonb_build_object(
        'found', true,
        'source', 'LOCAL',
        'payload', jsonb_build_object(
            'entity_id', i.entity_id,
            'instrument_id', i.instrument_id,
            'symbol', i.symbol,
            'name', e.name,
            'exchange', i.exchange,
            'exchange_code', i.exchange,
            'type', i.instrument_type,
            'instrument_type', i.instrument_type,
            'currency', i.currency_code,
            'currency_code', i.currency_code,
            'data_source', i.data_source,
            'last_price', i.last_price,
            'price_as_of', i.price_as_of
        )
    )
    INTO v_local
    FROM public.instruments i
    JOIN public.entities e
      ON e.entity_id = i.entity_id
    WHERE UPPER(BTRIM(i.symbol)) = v_symbol
    ORDER BY
        CASE
            WHEN NULLIF(BTRIM(i.currency_code), '') IS NOT NULL THEN 0
            ELSE 1
        END,
        CASE
            WHEN i.last_price IS NOT NULL THEN 0
            ELSE 1
        END,
        i.updated_at DESC,
        i.instrument_id
    LIMIT 1;

    IF v_local IS NOT NULL THEN
        RETURN v_local;
    END IF;

    ---------------------------------------------------------------------------
    -- CACHE
    ---------------------------------------------------------------------------

    v_cache := public.sp_get_market_cache(
        LOWER(v_symbol),
        p_cache_hours
    );

    IF v_cache IS NOT NULL
       AND jsonb_typeof(v_cache) = 'array'
    THEN
        SELECT item
        INTO v_cache_item
        FROM jsonb_array_elements(v_cache) AS item
        WHERE UPPER(item->>'symbol') = v_symbol
        LIMIT 1;

        IF v_cache_item IS NOT NULL THEN
            RETURN jsonb_build_object(
                'found', true,
                'source', 'CACHE',
                'payload', v_cache_item
            );
        END IF;
    END IF;

    ---------------------------------------------------------------------------
    -- MISS
    ---------------------------------------------------------------------------

    RETURN jsonb_build_object(
        'found', false,
        'source', 'MISS',
        'payload', NULL
    );
END;
$$;


--
-- Name: sp_get_user_catalyst_facets(uuid, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_catalyst_facets(p_user_id uuid, p_scope character varying DEFAULT 'MY_ASSETS'::character varying) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
WITH rows AS (
    SELECT *
    FROM public.sp_get_user_catalysts(
        p_user_id,
        p_scope,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        2000
    )
)
SELECT jsonb_build_object(
    'types', COALESCE((
        SELECT jsonb_agg(value ORDER BY value)
        FROM (SELECT DISTINCT catalyst_type AS value FROM rows WHERE catalyst_type IS NOT NULL) q
    ), '[]'::jsonb),
    'statuses', COALESCE((
        SELECT jsonb_agg(value ORDER BY value)
        FROM (SELECT DISTINCT status AS value FROM rows WHERE status IS NOT NULL) q
    ), '[]'::jsonb),
    'sources', COALESCE((
        SELECT jsonb_agg(value ORDER BY value)
        FROM (SELECT DISTINCT source AS value FROM rows WHERE source IS NOT NULL) q
    ), '[]'::jsonb)
);
$$;


--
-- Name: sp_get_user_catalysts(uuid, character varying, timestamp with time zone, timestamp with time zone, character varying, character varying, numeric, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_catalysts(p_user_id uuid, p_scope character varying DEFAULT 'MY_ASSETS'::character varying, p_date_from timestamp with time zone DEFAULT NULL::timestamp with time zone, p_date_to timestamp with time zone DEFAULT NULL::timestamp with time zone, p_catalyst_type character varying DEFAULT NULL::character varying, p_status character varying DEFAULT NULL::character varying, p_importance_min numeric DEFAULT NULL::numeric, p_query character varying DEFAULT NULL::character varying, p_limit integer DEFAULT 500) RETURNS TABLE(catalyst_id uuid, entity_id uuid, entity_name character varying, symbol character varying, exchange character varying, catalyst_type character varying, catalyst_date timestamp with time zone, status character varying, importance numeric, title text, source character varying, raw_payload jsonb, in_portfolio boolean, in_watchlist boolean, asset_scope character varying)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_scope varchar;
    v_query varchar;
BEGIN
    v_scope := upper(coalesce(nullif(btrim(p_scope), ''), 'MY_ASSETS'));
    v_query := nullif(btrim(p_query), '');

    IF v_scope NOT IN ('MY_ASSETS', 'PORTFOLIO', 'WATCHLIST', 'ALL') THEN
        RAISE EXCEPTION 'Unsupported catalyst scope: %', p_scope;
    END IF;
    IF p_limit < 1 OR p_limit > 2000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 2000';
    END IF;

    RETURN QUERY
    WITH preferred_instrument AS (
        SELECT DISTINCT ON (i.entity_id)
            i.entity_id,
            i.instrument_id,
            i.symbol,
            i.exchange
        FROM public.instruments i
        ORDER BY
            i.entity_id,
            (i.symbol IS NULL),
            (i.exchange IS NULL),
            i.updated_at DESC NULLS LAST,
            i.created_at DESC NULLS LAST,
            i.instrument_id
    ), enriched AS (
        SELECT
            c.catalyst_id,
            c.entity_id,
            e.name AS entity_name,
            pi.symbol,
            pi.exchange,
            c.catalyst_type,
            c.catalyst_date,
            c.status,
            c.importance,
            c.title,
            c.source,
            coalesce(c.raw_payload, '{}'::jsonb) || jsonb_build_object(
                'asset_match', asset_match.payload
            ) AS raw_payload,
            (asset_match.payload ->> 'in_portfolio')::boolean AS in_portfolio,
            (asset_match.payload ->> 'in_watchlist')::boolean AS in_watchlist,
            (asset_match.payload ->> 'asset_scope')::varchar AS asset_scope
        FROM public.catalysts c
        JOIN public.entities e
          ON e.entity_id = c.entity_id
        LEFT JOIN preferred_instrument pi
          ON pi.entity_id = c.entity_id
        CROSS JOIN LATERAL (
            SELECT public.sp_get_user_entity_asset_match(
                p_user_id,
                c.entity_id,
                pi.instrument_id
            ) AS payload
        ) asset_match
    )
    SELECT
        row.catalyst_id,
        row.entity_id,
        row.entity_name,
        row.symbol,
        row.exchange,
        row.catalyst_type,
        row.catalyst_date,
        row.status,
        row.importance,
        row.title,
        row.source,
        row.raw_payload,
        row.in_portfolio,
        row.in_watchlist,
        row.asset_scope
    FROM enriched row
    WHERE (p_date_from IS NULL OR row.catalyst_date >= p_date_from)
      AND (p_date_to IS NULL OR row.catalyst_date <= p_date_to)
      AND (
          nullif(btrim(p_catalyst_type), '') IS NULL
          OR upper(row.catalyst_type) = upper(btrim(p_catalyst_type))
      )
      AND (
          nullif(btrim(p_status), '') IS NULL
          OR upper(row.status) = upper(btrim(p_status))
      )
      AND (p_importance_min IS NULL OR row.importance >= p_importance_min)
      AND (
          v_query IS NULL
          OR row.entity_name ILIKE '%' || v_query || '%'
          OR row.symbol ILIKE '%' || v_query || '%'
          OR row.title ILIKE '%' || v_query || '%'
          OR row.source ILIKE '%' || v_query || '%'
          OR row.raw_payload -> 'asset_match' -> 'portfolio_symbols'
              @> to_jsonb(ARRAY[v_query]::text[])
          OR (row.raw_payload -> 'asset_match' -> 'portfolio_symbols')::text
              ILIKE '%' || v_query || '%'
      )
      AND CASE v_scope
          WHEN 'MY_ASSETS' THEN row.in_portfolio OR row.in_watchlist
          WHEN 'PORTFOLIO' THEN row.in_portfolio
          WHEN 'WATCHLIST' THEN row.in_watchlist
          ELSE true
      END
    ORDER BY
        row.catalyst_date ASC NULLS LAST,
        row.importance DESC NULLS LAST,
        row.entity_name,
        row.catalyst_id
    LIMIT p_limit;
END;
$$;


--
-- Name: sp_get_user_context(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_context(p_user_id uuid) RETURNS TABLE(user_id uuid, username character varying, email character varying, display_name character varying, is_active boolean, reporting_currency character, roles jsonb)
    LANGUAGE sql STABLE
    AS $$ SELECT u.user_id,u.username,u.email,u.display_name,u.is_active,u.reporting_currency,coalesce((SELECT jsonb_agg(r.role_key ORDER BY r.role_key) FROM user_roles ur JOIN roles r USING(role_id) WHERE ur.user_id=u.user_id),'[]') FROM users u WHERE u.user_id=p_user_id $$;


--
-- Name: sp_get_user_entity_asset_match(uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_entity_asset_match(p_user_id uuid, p_entity_id uuid, p_instrument_id uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
WITH portfolio_rows AS (
    SELECT
        pos.position_id,
        pf.portfolio_id,
        pf.name AS portfolio_name,
        i.instrument_id,
        i.symbol,
        i.exchange,
        i.currency_code,
        i.entity_id,
        pos.quantity,
        CASE
            WHEN p_instrument_id IS NOT NULL
             AND i.instrument_id = p_instrument_id
                THEN 'EXACT_INSTRUMENT'
            ELSE 'SAME_ENTITY'
        END AS match_type
    FROM public.positions pos
    JOIN public.portfolios pf
      ON pf.portfolio_id = pos.portfolio_id
     AND pf.user_id = p_user_id
    JOIN public.instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE p_entity_id IS NOT NULL
      AND i.entity_id = p_entity_id
), watchlist_rows AS (
    SELECT
        wi.watchlist_item_id,
        w.watchlist_id,
        w.name AS watchlist_name,
        wi.entity_id
    FROM public.watchlist_items wi
    JOIN public.watchlists w
      ON w.watchlist_id = wi.watchlist_id
     AND w.user_id = p_user_id
    WHERE p_entity_id IS NOT NULL
      AND wi.entity_id = p_entity_id
), portfolio_summary AS (
    SELECT
        count(*)::integer AS position_count,
        CASE
            WHEN bool_or(match_type = 'EXACT_INSTRUMENT')
                THEN 'EXACT_INSTRUMENT'
            WHEN count(*) > 0
                THEN 'SAME_ENTITY'
            ELSE 'NO_MATCH'
        END AS match_type,
        coalesce(
            jsonb_agg(DISTINCT symbol)
                FILTER (WHERE symbol IS NOT NULL),
            '[]'::jsonb
        ) AS symbols,
        coalesce(
            jsonb_agg(DISTINCT portfolio_name)
                FILTER (WHERE portfolio_name IS NOT NULL),
            '[]'::jsonb
        ) AS portfolio_names,
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'position_id', position_id,
                    'portfolio_id', portfolio_id,
                    'portfolio_name', portfolio_name,
                    'instrument_id', instrument_id,
                    'symbol', symbol,
                    'exchange', exchange,
                    'currency_code', currency_code,
                    'quantity', quantity,
                    'match_type', match_type
                )
                ORDER BY portfolio_name, symbol, position_id
            ),
            '[]'::jsonb
        ) AS positions
    FROM portfolio_rows
), watchlist_summary AS (
    SELECT
        count(*)::integer AS item_count,
        coalesce(
            jsonb_agg(DISTINCT watchlist_name)
                FILTER (WHERE watchlist_name IS NOT NULL),
            '[]'::jsonb
        ) AS watchlist_names,
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'watchlist_item_id', watchlist_item_id,
                    'watchlist_id', watchlist_id,
                    'watchlist_name', watchlist_name,
                    'entity_id', entity_id
                )
                ORDER BY watchlist_name, watchlist_item_id
            ),
            '[]'::jsonb
        ) AS items
    FROM watchlist_rows
)
SELECT jsonb_build_object(
    'in_portfolio', portfolio_summary.position_count > 0,
    'in_watchlist', watchlist_summary.item_count > 0,
    'asset_scope', CASE
        WHEN portfolio_summary.position_count > 0
         AND watchlist_summary.item_count > 0 THEN 'BOTH'
        WHEN portfolio_summary.position_count > 0 THEN 'PORTFOLIO'
        WHEN watchlist_summary.item_count > 0 THEN 'WATCHLIST'
        ELSE 'GLOBAL'
    END,
    'portfolio_match_type', portfolio_summary.match_type,
    'portfolio_position_count', portfolio_summary.position_count,
    'portfolio_symbols', portfolio_summary.symbols,
    'portfolio_names', portfolio_summary.portfolio_names,
    'portfolio_positions', portfolio_summary.positions,
    'watchlist_item_count', watchlist_summary.item_count,
    'watchlist_names', watchlist_summary.watchlist_names,
    'watchlist_items', watchlist_summary.items
)
FROM portfolio_summary
CROSS JOIN watchlist_summary;
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
-- Name: sp_get_user_notification_preferences(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_notification_preferences(p_user_id uuid) RETURNS TABLE(notification_type character varying, label character varying, description text, enabled boolean, source_id uuid, severity_min character varying)
    LANGUAGE sql STABLE
    AS $$
WITH notification_types(notification_type, label, description, default_enabled) AS (
    VALUES
        ('UPCOMING_CATALYST'::varchar, 'Catalyst imminenti'::varchar,
         'Eventi attesi nei prossimi giorni.'::text, true),
        ('BREAKING_CATALYST'::varchar, 'Catalyst urgenti'::varchar,
         'Notizie ed eventi rilevanti appena rilevati.'::text, true),
        ('DAILY_MOVER'::varchar, 'Movimenti giornalieri'::varchar,
         'Variazioni di prezzo rilevanti rilevate durante la giornata.'::text, false)
), default_source AS (
    SELECT us.source_id
    FROM public.user_sources us
    WHERE us.user_id = p_user_id
      AND COALESCE((us.config->>'enabled')::boolean, true) = true
    ORDER BY us.created_at DESC
    LIMIT 1
)
SELECT
    nt.notification_type,
    nt.label,
    nt.description,
    COALESCE(ns.enabled, nt.default_enabled),
    COALESCE(ns.source_id, ds.source_id),
    ns.severity_min
FROM notification_types nt
LEFT JOIN public.notification_subscriptions ns
  ON ns.user_id = p_user_id
 AND ns.portfolio_id IS NULL
 AND UPPER(ns.event_type) = nt.notification_type
LEFT JOIN default_source ds ON true
ORDER BY CASE nt.notification_type
    WHEN 'UPCOMING_CATALYST' THEN 1
    WHEN 'BREAKING_CATALYST' THEN 2
    ELSE 3
END;
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
-- Name: sp_get_user_notification_source_config(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_notification_source_config(p_user_id uuid, p_source_id uuid) RETURNS TABLE(source_id uuid, source_type character varying, source_name character varying, external_id text, config jsonb)
    LANGUAGE sql STABLE
    AS $$
SELECT
    us.source_id,
    UPPER(us.source_type)::varchar,
    us.source_name,
    us.external_id,
    COALESCE(us.config, '{}'::jsonb)
FROM public.user_sources us
WHERE us.user_id = p_user_id
  AND us.source_id = p_source_id;
$$;


--
-- Name: sp_get_user_notification_sources(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_user_notification_sources(p_user_id uuid) RETURNS TABLE(source_id uuid, channel character varying, source_name character varying, external_id_masked text, enabled boolean, credential_configured boolean, created_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
SELECT
    us.source_id,
    UPPER(us.source_type)::varchar,
    us.source_name,
    CASE
        WHEN us.external_id IS NULL THEN NULL
        WHEN length(us.external_id) <= 4 THEN repeat('*', length(us.external_id))
        ELSE repeat('*', GREATEST(length(us.external_id) - 4, 4))
             || right(us.external_id, 4)
    END,
    COALESCE((us.config->>'enabled')::boolean, true),
    COALESCE(NULLIF(us.config->>'credential_encrypted', ''),
             NULLIF(us.config->>'bot_token', ''),
             NULLIF(us.config->>'token', '')) IS NOT NULL,
    us.created_at
FROM public.user_sources us
WHERE us.user_id = p_user_id
ORDER BY us.created_at, us.source_name;
$$;


--
-- Name: sp_get_watchlist_items(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_watchlist_items(p_watchlist_id uuid, p_user_id uuid) RETURNS TABLE(watchlist_item_id uuid, watchlist_id uuid, entity_id uuid, priority smallint, target_price numeric, notes text, name character varying, instrument_id uuid, symbol character varying, exchange character varying, currency_code character, last_price numeric)
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


--
-- Name: sp_get_watchlist_items_with_pricing(uuid, uuid, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_get_watchlist_items_with_pricing(p_watchlist_id uuid, p_user_id uuid, p_max_age_hours numeric DEFAULT 96) RETURNS SETOF jsonb
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
    LANGUAGE sql
    AS $$
    SELECT r.entity_id, r.instrument_id, r.imported
    FROM public.sp_upsert_asset_profile(p_payload, NULL, NULL, true) r;
$$;


--
-- Name: sp_list_orphan_entities(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_list_orphan_entities() RETURNS TABLE(entity_id uuid)
    LANGUAGE sql
    AS $$
    SELECT e.entity_id
    FROM public.entities e
    WHERE NOT EXISTS (
        SELECT 1 FROM public.instruments i WHERE i.entity_id = e.entity_id
    )
    ORDER BY e.entity_id;
$$;


--
-- Name: sp_list_scheduler_job_runs(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_list_scheduler_job_runs(p_job_key character varying, p_limit integer DEFAULT 20) RETURNS TABLE(job_run_id uuid, job_id uuid, status character varying, started_at timestamp with time zone, completed_at timestamp with time zone, duration_seconds numeric, error_message text, result jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        sjr.job_run_id,
        sjr.job_id,
        sjr.status,
        sjr.started_at,
        sjr.completed_at,
        CASE
            WHEN sjr.completed_at IS NULL THEN
                EXTRACT(EPOCH FROM (now() - sjr.started_at))::numeric
            ELSE
                EXTRACT(EPOCH FROM (
                    sjr.completed_at - sjr.started_at
                ))::numeric
        END AS duration_seconds,
        sjr.error_message,
        sjr.result
    FROM public.scheduler_job_runs sjr
    JOIN public.scheduler_jobs sj
      ON sj.job_id = sjr.job_id
    WHERE sj.job_key = p_job_key
    ORDER BY sjr.started_at DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100);
$$;


--
-- Name: sp_list_scheduler_jobs(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_list_scheduler_jobs() RETURNS TABLE(job_id uuid, job_key character varying, job_name character varying, python_module character varying, trigger_type character varying, cron_expression character varying, interval_minutes integer, enabled boolean, last_run_at timestamp with time zone, next_run_at timestamp with time zone, last_status character varying, last_error text, updated_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        sj.job_id,
        sj.job_key,
        sj.job_name,
        sj.python_module,
        sj.trigger_type,
        sj.cron_expression,
        sj.interval_minutes,
        sj.enabled,
        latest_run.started_at AS last_run_at,
        CASE
            WHEN sj.enabled THEN sj.next_run_at
            ELSE NULL
        END AS next_run_at,
        latest_run.status AS last_status,
        latest_run.error_message AS last_error,
        sj.updated_at
    FROM public.scheduler_jobs sj
    LEFT JOIN LATERAL (
        SELECT
            sjr.started_at,
            sjr.status,
            sjr.error_message
        FROM public.scheduler_job_runs sjr
        WHERE sjr.job_id = sj.job_id
        ORDER BY sjr.started_at DESC
        LIMIT 1
    ) latest_run ON TRUE
    ORDER BY sj.job_name;
$$;


--
-- Name: sp_llm_claim_cached_response(character varying, character varying, character varying, jsonb, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_llm_claim_cached_response(p_cache_key character varying, p_provider character varying, p_model character varying, p_request_payload jsonb, p_ttl_seconds integer, p_claim_stale_seconds integer) RETURNS TABLE(action character varying, response_data jsonb, response_raw jsonb, sources jsonb, search_executed boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_row public.llm_response_cache%ROWTYPE;
BEGIN
    IF length(p_cache_key) <> 64 THEN
        RAISE EXCEPTION 'p_cache_key must contain a SHA-256 hex digest';
    END IF;
    IF p_ttl_seconds <= 0 OR p_claim_stale_seconds <= 0 THEN
        RAISE EXCEPTION 'LLM cache timing values must be positive';
    END IF;

    INSERT INTO public.llm_response_cache(
        cache_key, provider, model, status, request_payload,
        expires_at, claimed_at
    ) VALUES (
        p_cache_key, p_provider, p_model, 'PROCESSING', p_request_payload,
        now() + make_interval(secs => p_ttl_seconds), now()
    )
    ON CONFLICT (cache_key) DO NOTHING;

    IF FOUND THEN
        RETURN QUERY SELECT 'CLAIM'::varchar, NULL::jsonb, NULL::jsonb,
            '[]'::jsonb, false;
        RETURN;
    END IF;

    SELECT * INTO v_row
    FROM public.llm_response_cache
    WHERE cache_key = p_cache_key
    FOR UPDATE;

    IF v_row.status = 'READY' AND v_row.expires_at > now() THEN
        UPDATE public.llm_response_cache
        SET hit_count = hit_count + 1,
            last_accessed_at = now()
        WHERE cache_key = p_cache_key;
        RETURN QUERY SELECT 'HIT'::varchar, v_row.response_data,
            v_row.response_raw, v_row.sources, v_row.search_executed;
        RETURN;
    END IF;

    IF v_row.status = 'READY'
       OR v_row.claimed_at IS NULL
       OR v_row.claimed_at < now() - make_interval(secs => p_claim_stale_seconds)
    THEN
        UPDATE public.llm_response_cache
        SET provider = p_provider,
            model = p_model,
            status = 'PROCESSING',
            request_payload = p_request_payload,
            response_data = NULL,
            response_raw = NULL,
            sources = '[]'::jsonb,
            search_executed = false,
            expires_at = now() + make_interval(secs => p_ttl_seconds),
            claimed_at = now(),
            last_error = NULL
        WHERE cache_key = p_cache_key;
        RETURN QUERY SELECT 'CLAIM'::varchar, NULL::jsonb, NULL::jsonb,
            '[]'::jsonb, false;
        RETURN;
    END IF;

    RETURN QUERY SELECT 'WAIT'::varchar, NULL::jsonb, NULL::jsonb,
        '[]'::jsonb, false;
END;
$$;


--
-- Name: sp_llm_complete_cached_response(character varying, jsonb, jsonb, jsonb, boolean, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_llm_complete_cached_response(p_cache_key character varying, p_response_data jsonb, p_response_raw jsonb, p_sources jsonb, p_search_executed boolean, p_ttl_seconds integer) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.llm_response_cache
    SET status = 'READY',
        response_data = p_response_data,
        response_raw = p_response_raw,
        sources = COALESCE(p_sources, '[]'::jsonb),
        search_executed = COALESCE(p_search_executed, false),
        expires_at = now() + make_interval(secs => p_ttl_seconds),
        claimed_at = NULL,
        last_accessed_at = now(),
        last_error = NULL
    WHERE cache_key = p_cache_key
      AND status = 'PROCESSING';
    RETURN FOUND;
END;
$$;


--
-- Name: sp_llm_delete_expired_responses(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_llm_delete_expired_responses(p_limit integer DEFAULT 1000) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted integer;
BEGIN
    WITH expired AS (
        SELECT cache_key
        FROM public.llm_response_cache
        WHERE status = 'READY' AND expires_at <= now()
        ORDER BY expires_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM public.llm_response_cache c
    USING expired e
    WHERE c.cache_key = e.cache_key;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;


--
-- Name: sp_llm_release_cached_response(character varying, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_llm_release_cached_response(p_cache_key character varying, p_error text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM public.llm_response_cache
    WHERE cache_key = p_cache_key
      AND status = 'PROCESSING';
    RETURN FOUND;
END;
$$;


--
-- Name: sp_maintain_llm_queue(integer, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_maintain_llm_queue(p_completed_retention_days integer, p_failed_retention_days integer, p_abandoned_retention_days integer) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_recovered integer := 0;
    v_deleted_completed integer := 0;
    v_deleted_failed integer := 0;
    v_deleted_abandoned integer := 0;
BEGIN
    IF p_completed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_completed_retention_days must be >= 1';
    END IF;

    IF p_failed_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_failed_retention_days must be >= 1';
    END IF;

    IF p_abandoned_retention_days < 1 THEN
        RAISE EXCEPTION
            'p_abandoned_retention_days must be >= 1';
    END IF;

    UPDATE public.llm_request_queue
    SET
        status = CASE
            WHEN attempt_count < max_attempts
                THEN 'RETRY'
            ELSE 'FAILED'
        END,
        available_at = CASE
            WHEN attempt_count < max_attempts
                THEN now()
            ELSE available_at
        END,
        locked_at = NULL,
        locked_by = NULL,
        lease_expires_at = NULL,
        completed_at = CASE
            WHEN attempt_count >= max_attempts
                THEN now()
            ELSE NULL
        END,
        last_error = LEFT(
            COALESCE(
                last_error || E'\n',
                ''
            )
            || 'Recovered after expired processing lease',
            4000
        )
    WHERE status = 'PROCESSING'
      AND lease_expires_at IS NOT NULL
      AND lease_expires_at <= now();

    GET DIAGNOSTICS v_recovered = ROW_COUNT;

    DELETE FROM public.llm_request_queue
    WHERE status = 'COMPLETED'
      AND completed_at <
          now() - make_interval(
              days => p_completed_retention_days
          );

    GET DIAGNOSTICS
        v_deleted_completed = ROW_COUNT;

    DELETE FROM public.llm_request_queue
    WHERE status = 'FAILED'
      AND completed_at <
          now() - make_interval(
              days => p_failed_retention_days
          );

    GET DIAGNOSTICS
        v_deleted_failed = ROW_COUNT;

    DELETE FROM public.llm_request_queue
    WHERE status IN ('PENDING', 'RETRY')
      AND created_at <
          now() - make_interval(
              days => p_abandoned_retention_days
          );

    GET DIAGNOSTICS
        v_deleted_abandoned = ROW_COUNT;

    RETURN jsonb_build_object(
        'recovered', v_recovered,
        'deleted_completed', v_deleted_completed,
        'deleted_failed', v_deleted_failed,
        'deleted_abandoned', v_deleted_abandoned
    );
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
-- Name: sp_market_analysis_apply_llm_enrichment(uuid, character varying, numeric, jsonb, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_apply_llm_enrichment(p_signal_id uuid, p_enrichment_fingerprint character varying, p_llm_score numeric, p_llm_analysis jsonb, p_composite_score numeric DEFAULT NULL::numeric) RETURNS boolean
    LANGUAGE plpgsql
    AS $_$
BEGIN
    IF p_signal_id IS NULL THEN
        RAISE EXCEPTION 'p_signal_id is required';
    END IF;

    IF NULLIF(BTRIM(p_enrichment_fingerprint), '') IS NULL THEN
        RAISE EXCEPTION 'p_enrichment_fingerprint cannot be empty';
    END IF;

    IF BTRIM(p_enrichment_fingerprint) !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION
            'p_enrichment_fingerprint must be a lowercase SHA-256 hexadecimal value';
    END IF;

    IF p_llm_score IS NULL OR p_llm_score < 0 OR p_llm_score > 100 THEN
        RAISE EXCEPTION 'p_llm_score must be between 0 and 100';
    END IF;

    IF p_llm_analysis IS NULL OR jsonb_typeof(p_llm_analysis) <> 'object' THEN
        RAISE EXCEPTION 'p_llm_analysis must be a JSON object';
    END IF;

    IF p_composite_score IS NOT NULL
       AND (p_composite_score < 0 OR p_composite_score > 100) THEN
        RAISE EXCEPTION 'p_composite_score must be between 0 and 100';
    END IF;

    UPDATE public.trading_signals
    SET llm_score = p_llm_score,
        composite_score = COALESCE(p_composite_score, composite_score),
        llm_analysis = p_llm_analysis,
        enrichment_fingerprint = BTRIM(p_enrichment_fingerprint)
    WHERE signal_id = p_signal_id
      AND status IN ('CANDIDATE', 'CONFIRMED');

    RETURN FOUND;
END;
$_$;


--
-- Name: sp_market_analysis_close_missing_signals(timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_close_missing_signals(p_observed_before timestamp with time zone, p_stability_minutes integer DEFAULT 30) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE n integer;
BEGIN
 WITH latest AS (SELECT DISTINCT ON(instrument_id,strategy_code,timeframe) * FROM public.market_analysis_signal_events ORDER BY instrument_id,strategy_code,timeframe,observed_at DESC), inserted AS (
 INSERT INTO public.market_analysis_signal_events(signal_id,instrument_id,strategy_code,timeframe,previous_status,new_status,previous_direction,new_direction,event_type,reason,observed_at,payload)
 SELECT signal_id,instrument_id,strategy_code,timeframe,new_status,'EXPIRED',new_direction,new_direction,'EXPIRED','not observed in subsequent scan',now(),'{}'::jsonb FROM latest WHERE new_status IN('CANDIDATE','CONFIRMED') AND observed_at < p_observed_before-make_interval(mins=>GREATEST(p_stability_minutes,0)) RETURNING signal_id)
 UPDATE public.trading_signals SET status='EXPIRED' WHERE signal_id IN(SELECT signal_id FROM inserted); GET DIAGNOSTICS n=ROW_COUNT; RETURN n;
END;$$;


--
-- Name: sp_market_analysis_expire_evaluated_signals(jsonb, uuid[], integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_expire_evaluated_signals(p_scopes jsonb, p_signal_ids uuid[], p_stability_minutes integer DEFAULT 30) RETURNS integer
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


--
-- Name: sp_market_analysis_finish_scan_run(uuid, character varying, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_finish_scan_run(p_run_id uuid, p_status character varying, p_summary jsonb DEFAULT '{}'::jsonb) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE v_status varchar;
BEGIN
    v_status := UPPER(BTRIM(p_status));
    IF v_status NOT IN ('COMPLETED', 'FAILED') THEN
        RAISE EXCEPTION 'Unsupported final scan status: %', p_status;
    END IF;
    UPDATE public.market_analysis_scan_runs
    SET status = v_status,
        summary = COALESCE(p_summary, '{}'::jsonb),
        finished_at = now()
    WHERE run_id = p_run_id AND status = 'RUNNING';
    RETURN FOUND;
END;
$$;


--
-- Name: sp_market_analysis_get_universe(character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_get_universe(p_code character varying) RETURNS TABLE(universe_id uuid, code character varying, name character varying, universe_type character varying, config jsonb)
    LANGUAGE sql STABLE
    AS $$
    SELECT mu.universe_id, mu.code, mu.name, mu.universe_type, mu.config
    FROM public.market_universes mu
    WHERE UPPER(mu.code) = UPPER(BTRIM(p_code))
      AND mu.active = true
    LIMIT 1;
$$;


--
-- Name: sp_market_analysis_list_pending_llm_enrichment(character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_list_pending_llm_enrichment(p_enrichment_fingerprint character varying, p_limit integer DEFAULT 25) RETURNS TABLE(signal_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, entity_name character varying, strategy_code character varying, timeframe character varying, direction character varying, signal_type character varying, trend character varying, status character varying, confidence numeric, technical_score numeric, financial_score numeric, catalyst_score numeric, reference_price numeric, invalidation_price numeric, target_price numeric, observed_at timestamp with time zone, evidence jsonb)
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


--
-- Name: sp_market_analysis_list_signal_events(uuid, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_list_signal_events(p_instrument_id uuid, p_strategy_code character varying DEFAULT NULL::character varying, p_limit integer DEFAULT 100) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
 SELECT to_jsonb(e) FROM public.market_analysis_signal_events e WHERE e.instrument_id=p_instrument_id AND(p_strategy_code IS NULL OR e.strategy_code=p_strategy_code) ORDER BY e.observed_at DESC,e.created_at DESC LIMIT LEAST(GREATEST(p_limit,1),1000);
$$;


--
-- Name: sp_market_analysis_list_universe_members(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_list_universe_members(p_universe_id uuid) RETURNS TABLE(universe_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, currency_code character, isin character varying)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        mum.universe_id,
        i.instrument_id,
        i.entity_id,
        i.symbol,
        i.exchange,
        i.currency_code,
        (
            SELECT id.identity_value
            FROM public.instrument_identities ii
            JOIN public.identities id
              ON id.identity_id = ii.identity_id
            JOIN public.identity_types it
              ON it.identity_type_id = id.identity_type_id
            WHERE ii.instrument_id = i.instrument_id
              AND it.target_kind = 'INSTRUMENT'
              AND UPPER(it.code) = 'ISIN'
            ORDER BY
                ii.is_primary DESC,
                ii.verified_at DESC NULLS LAST,
                ii.created_at DESC,
                id.identity_id
            LIMIT 1
        ) AS isin
    FROM public.market_universe_members mum
    JOIN public.instruments i ON i.instrument_id = mum.instrument_id
    WHERE mum.universe_id = p_universe_id
      AND mum.valid_to IS NULL
    ORDER BY i.symbol, i.exchange NULLS LAST, i.instrument_id;
$$;


--
-- Name: sp_market_analysis_reconcile_signal_lifecycle(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_reconcile_signal_lifecycle(p_signal_id uuid, p_stability_minutes integer DEFAULT 30) RETURNS jsonb
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


--
-- Name: sp_market_analysis_record_scan_failure(uuid, uuid, character varying, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_record_scan_failure(p_run_id uuid, p_instrument_id uuid, p_symbol character varying, p_reason text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE v_failure_id uuid;
BEGIN
    INSERT INTO public.market_analysis_scan_failures
        (run_id, instrument_id, symbol, reason)
    VALUES
        (p_run_id, p_instrument_id, BTRIM(p_symbol), BTRIM(p_reason))
    RETURNING failure_id INTO v_failure_id;
    RETURN v_failure_id;
END;
$$;


--
-- Name: sp_market_analysis_replace_universe_members(uuid, jsonb, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_replace_universe_members(p_universe_id uuid, p_members jsonb, p_effective_at timestamp with time zone) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_added integer := 0;
    v_removed integer := 0;
    v_requested_count integer := 0;
BEGIN
    IF p_universe_id IS NULL OR NOT EXISTS (
        SELECT 1
        FROM public.market_universes mu
        WHERE mu.universe_id = p_universe_id
    ) THEN
        RAISE EXCEPTION 'Universe not found: %', p_universe_id;
    END IF;
    IF p_effective_at IS NULL THEN
        RAISE EXCEPTION 'p_effective_at is required';
    END IF;
    IF p_members IS NULL OR jsonb_typeof(p_members) <> 'array' THEN
        RAISE EXCEPTION 'p_members must be a JSON array';
    END IF;

    SELECT COUNT(*)
    INTO v_requested_count
    FROM jsonb_array_elements(p_members);

    IF v_requested_count = 0 THEN
        RAISE EXCEPTION 'Refusing to replace an universe with an empty member set';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_members) item
        LEFT JOIN public.instruments i
          ON i.instrument_id = NULLIF(item->>'instrument_id', '')::uuid
        WHERE i.instrument_id IS NULL
    ) THEN
        RAISE EXCEPTION 'p_members contains unknown or missing instrument_id values';
    END IF;

    UPDATE public.market_universe_members mum
    SET valid_to = p_effective_at
    WHERE mum.universe_id = p_universe_id
      AND mum.valid_to IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements(p_members) item
          WHERE (item->>'instrument_id')::uuid = mum.instrument_id
      );
    GET DIAGNOSTICS v_removed = ROW_COUNT;

    INSERT INTO public.market_universe_members
    (
        universe_id,
        instrument_id,
        valid_from,
        source_reference,
        raw_payload
    )
    SELECT
        p_universe_id,
        (item->>'instrument_id')::uuid,
        p_effective_at,
        NULLIF(BTRIM(item->>'source_reference'), ''),
        COALESCE(item->'raw_payload', '{}'::jsonb)
    FROM jsonb_array_elements(p_members) item
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.market_universe_members existing
        WHERE existing.universe_id = p_universe_id
          AND existing.instrument_id = (item->>'instrument_id')::uuid
          AND existing.valid_to IS NULL
    );
    GET DIAGNOSTICS v_added = ROW_COUNT;

    RETURN jsonb_build_object(
        'requested', v_requested_count,
        'added', v_added,
        'removed', v_removed
    );
END;
$$;


--
-- Name: sp_market_analysis_resolve_universe_symbols(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_resolve_universe_symbols(p_symbols jsonb) RETURNS TABLE(requested_symbol character varying, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    IF p_symbols IS NULL OR jsonb_typeof(p_symbols) <> 'array' THEN
        RAISE EXCEPTION 'p_symbols must be a JSON array';
    END IF;

    RETURN QUERY
    WITH requested AS
    (
        SELECT DISTINCT
            UPPER(REPLACE(BTRIM(value #>> '{}'), '.', '-'))::varchar AS requested_symbol
        FROM jsonb_array_elements(p_symbols)
        WHERE NULLIF(BTRIM(value #>> '{}'), '') IS NOT NULL
    ),
    ranked AS
    (
        SELECT
            r.requested_symbol,
            i.instrument_id,
            i.entity_id,
            i.symbol,
            i.exchange,
            ROW_NUMBER() OVER
            (
                PARTITION BY r.requested_symbol
                ORDER BY
                    CASE WHEN UPPER(i.symbol) = r.requested_symbol THEN 0 ELSE 1 END,
                    CASE WHEN i.exchange IN ('XNAS', 'XNYS') THEN 0 ELSE 1 END,
                    i.instrument_id
            ) AS position
        FROM requested r
        JOIN public.instruments i
          ON UPPER(REPLACE(i.symbol, '.', '-')) = r.requested_symbol
    )
    SELECT
        ranked.requested_symbol,
        ranked.instrument_id,
        ranked.entity_id,
        ranked.symbol,
        ranked.exchange
    FROM ranked
    WHERE ranked.position = 1
    ORDER BY ranked.requested_symbol;
END;
$$;


--
-- Name: sp_market_analysis_start_scan_run(uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_start_scan_run(p_universe_id uuid, p_config jsonb DEFAULT '{}'::jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_run_id uuid;
BEGIN
    INSERT INTO public.market_analysis_scan_runs
    (
        universe_id,
        config
    )
    VALUES
    (
        p_universe_id,
        COALESCE(p_config, '{}'::jsonb)
    )
    RETURNING run_id INTO v_run_id;

    RETURN v_run_id;
END;
$$;


--
-- Name: sp_market_analysis_update_signal_status(uuid, character varying, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_update_signal_status(p_signal_id uuid, p_status character varying, p_close_reason text DEFAULT NULL::text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_status varchar;
BEGIN
    v_status := UPPER(BTRIM(p_status));
    IF v_status NOT IN ('CANDIDATE', 'CONFIRMED', 'INVALIDATED', 'EXPIRED', 'CLOSED') THEN
        RAISE EXCEPTION 'Unsupported signal status: %', p_status;
    END IF;

    UPDATE public.trading_signals
    SET
        status = v_status,
        confirmed_at = CASE
            WHEN v_status = 'CONFIRMED' AND confirmed_at IS NULL THEN now()
            ELSE confirmed_at
        END,
        closed_at = CASE
            WHEN v_status IN ('INVALIDATED', 'EXPIRED', 'CLOSED') THEN now()
            ELSE NULL
        END,
        close_reason = CASE
            WHEN v_status IN ('INVALIDATED', 'EXPIRED', 'CLOSED') THEN p_close_reason
            ELSE NULL
        END
    WHERE signal_id = p_signal_id;

    RETURN FOUND;
END;
$$;


--
-- Name: sp_market_analysis_upsert_signal(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_upsert_signal(p_payload jsonb) RETURNS uuid
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


--
-- Name: sp_market_analysis_upsert_universe(character varying, character varying, character varying, character varying, boolean, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_analysis_upsert_universe(p_code character varying, p_name character varying, p_universe_type character varying, p_source_system_code character varying DEFAULT NULL::character varying, p_active boolean DEFAULT true, p_config jsonb DEFAULT '{}'::jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_universe_id uuid;
    v_source_system_id uuid;
BEGIN
    IF NULLIF(BTRIM(p_code), '') IS NULL THEN
        RAISE EXCEPTION 'p_code cannot be empty';
    END IF;
    IF NULLIF(BTRIM(p_name), '') IS NULL THEN
        RAISE EXCEPTION 'p_name cannot be empty';
    END IF;
    IF NULLIF(BTRIM(p_universe_type), '') IS NULL THEN
        RAISE EXCEPTION 'p_universe_type cannot be empty';
    END IF;

    IF NULLIF(BTRIM(p_source_system_code), '') IS NOT NULL THEN
        SELECT es.system_id
        INTO v_source_system_id
        FROM public.external_systems es
        WHERE UPPER(es.code) = UPPER(BTRIM(p_source_system_code))
          AND es.active = true;

        IF v_source_system_id IS NULL THEN
            RAISE EXCEPTION 'Active external system not found: %', p_source_system_code;
        END IF;
    END IF;

    INSERT INTO public.market_universes
    (
        code,
        name,
        universe_type,
        source_system_id,
        active,
        config
    )
    VALUES
    (
        UPPER(BTRIM(p_code)),
        BTRIM(p_name),
        UPPER(BTRIM(p_universe_type)),
        v_source_system_id,
        p_active,
        COALESCE(p_config, '{}'::jsonb)
    )
    ON CONFLICT (UPPER(BTRIM(code)))
    DO UPDATE SET
        name = EXCLUDED.name,
        universe_type = EXCLUDED.universe_type,
        source_system_id = EXCLUDED.source_system_id,
        active = EXCLUDED.active,
        config = EXCLUDED.config
    RETURNING universe_id INTO v_universe_id;

    RETURN v_universe_id;
END;
$$;


--
-- Name: sp_market_data_enqueue_incomplete_instruments(boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_data_enqueue_incomplete_instruments(p_include_missing_price boolean DEFAULT false) RETURNS TABLE(candidates_count integer, enqueued_count integer)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_candidates_count integer := 0;
    v_enqueued_count integer := 0;
    v_instrument record;
BEGIN
    FOR v_instrument IN
        SELECT
            i.instrument_id,
            i.symbol,
            COALESCE(
                NULLIF(UPPER(BTRIM(i.data_source)), ''),
                'YAHOO'
            ) AS data_source
        FROM public.instruments i
        WHERE NULLIF(BTRIM(i.currency_code), '') IS NULL
           OR (
               p_include_missing_price
               AND (
                   i.last_price IS NULL
                   OR i.price_as_of IS NULL
               )
           )
        ORDER BY
            i.created_at,
            i.instrument_id
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
    SELECT
        v_candidates_count,
        v_enqueued_count;
END;
$$;


--
-- Name: sp_market_data_enqueue_user_prices(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_data_enqueue_user_prices(p_user_id uuid, p_max_age_seconds integer DEFAULT 30) RETURNS TABLE(candidates_count integer, enqueued_count integer)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_candidates_count integer := 0;
    v_enqueued_count integer := 0;
    v_instrument record;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'p_user_id is required';
    END IF;

    IF p_max_age_seconds < 1 THEN
        RAISE EXCEPTION 'p_max_age_seconds must be >= 1';
    END IF;

    FOR v_instrument IN
        WITH user_instruments AS (
            SELECT DISTINCT pos.instrument_id
            FROM public.positions pos
            JOIN public.portfolios pf
              ON pf.portfolio_id = pos.portfolio_id
            WHERE pf.user_id = p_user_id

            UNION

            SELECT DISTINCT i.instrument_id
            FROM public.watchlist_items wi
            JOIN public.watchlists w
              ON w.watchlist_id = wi.watchlist_id
            JOIN public.instruments i
              ON i.entity_id = wi.entity_id
            WHERE w.user_id = p_user_id
        )
        SELECT
            i.instrument_id,
            i.symbol,
            COALESCE(
                NULLIF(UPPER(BTRIM(i.data_source)), ''),
                'YAHOO'
            ) AS data_source
        FROM user_instruments ui
        JOIN public.instruments i
          ON i.instrument_id = ui.instrument_id
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


--
-- Name: sp_market_data_update_instrument_price(uuid, numeric, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_data_update_instrument_price(p_instrument_id uuid, p_last_price numeric, p_price_as_of timestamp with time zone) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
 IF p_last_price IS NULL OR p_price_as_of IS NULL THEN
  RAISE EXCEPTION 'last_price and price_as_of are required';
 END IF;
 UPDATE instruments
 SET last_price=p_last_price, price_as_of=p_price_as_of
 WHERE instrument_id=p_instrument_id AND (price_as_of IS NULL OR p_price_as_of >= price_as_of);
 GET DIAGNOSTICS v_count = ROW_COUNT;
 RETURN v_count > 0;
END; $$;


--
-- Name: sp_market_discovery_build_candidates(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_build_candidates(p_snapshot_id uuid DEFAULT NULL::uuid) RETURNS jsonb
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


--
-- Name: sp_market_discovery_create_snapshot(character varying, character varying, character varying, timestamp with time zone, integer, text, jsonb, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_create_snapshot(p_provider_code character varying, p_source_type character varying, p_source_name character varying, p_observed_at timestamp with time zone, p_requested_limit integer, p_source_reference text, p_source_metadata jsonb, p_items jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source_id uuid;
    v_snapshot_id uuid;
    v_item_count integer;
BEGIN
    IF p_observed_at IS NULL THEN RAISE EXCEPTION 'observed_at is required'; END IF;
    IF p_requested_limit < 1 THEN RAISE EXCEPTION 'requested_limit must be positive'; END IF;
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN RAISE EXCEPTION 'items must be a JSON array'; END IF;
    SELECT count(*) INTO v_item_count FROM jsonb_array_elements(p_items);
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(symbol text)
        WHERE NULLIF(btrim(x.symbol), '') IS NULL
    ) THEN RAISE EXCEPTION 'every item requires a symbol'; END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(symbol text)
        GROUP BY upper(btrim(x.symbol)) HAVING count(*) > 1
    ) THEN RAISE EXCEPTION 'items contain duplicate symbols'; END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(rank integer)
        WHERE x.rank IS NOT NULL GROUP BY x.rank HAVING count(*) > 1
    ) THEN RAISE EXCEPTION 'items contain duplicate ranks'; END IF;

    v_source_id := public.sp_market_discovery_upsert_source(
        p_provider_code, p_source_type, p_source_name, true
    );
    INSERT INTO public.market_discovery_snapshots(
        source_id, observed_at, requested_limit, returned_count, source_reference, source_metadata
    ) VALUES (
        v_source_id, p_observed_at, p_requested_limit, v_item_count,
        NULLIF(btrim(p_source_reference), ''), COALESCE(p_source_metadata, '{}'::jsonb)
    ) RETURNING snapshot_id INTO v_snapshot_id;

    INSERT INTO public.market_discovery_snapshot_items(
        snapshot_id, symbol, name, rank, previous_rank, mentions,
        previous_mentions, upvotes, score, raw_payload
    )
    SELECT v_snapshot_id, upper(btrim(x.symbol)), NULLIF(btrim(x.name), ''),
           x.rank, x.previous_rank, x.mentions, x.previous_mentions,
           x.upvotes, x.score, COALESCE(x.raw_payload, '{}'::jsonb)
    FROM jsonb_to_recordset(p_items) AS x(
        symbol text, name text, rank integer, previous_rank integer,
        mentions integer, previous_mentions integer, upvotes integer,
        score numeric, raw_payload jsonb
    );
    RETURN v_snapshot_id;
END;
$$;


--
-- Name: sp_market_discovery_delete_expired_candidates(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_delete_expired_candidates(p_retention_days integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_deleted integer;
BEGIN
    IF p_retention_days < 1 THEN
        RAISE EXCEPTION 'retention_days must be positive';
    END IF;

    DELETE FROM public.market_discovery_candidates c
    USING public.market_discovery_snapshots s
    WHERE s.snapshot_id = c.snapshot_id
      AND s.observed_at < now() - make_interval(days => p_retention_days);

    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;


--
-- Name: sp_market_discovery_delete_expired_snapshots(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_delete_expired_snapshots(p_retention_days integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_deleted integer;
BEGIN
    IF p_retention_days < 1 THEN RAISE EXCEPTION 'retention_days must be positive'; END IF;
    DELETE FROM public.market_discovery_snapshots
    WHERE observed_at < now() - make_interval(days => p_retention_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;


--
-- Name: sp_market_discovery_get_candidate(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_get_candidate(p_candidate_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
SELECT jsonb_build_object(
    'candidate_id', c.candidate_id,
    'snapshot_id', c.snapshot_id,
    'snapshot_item_id', c.snapshot_item_id,
    'entity_id', c.entity_id,
    'instrument_id', c.instrument_id,
    'symbol', c.symbol,
    'origin', c.origin,
    'resolution_status', c.resolution_status,
    'attention_score', c.attention_score,
    'universe_codes', c.universe_codes,
    'attention_evidence', c.attention_evidence,
    'created_at', c.created_at,
    'updated_at', c.updated_at
)
FROM public.market_discovery_candidates c
WHERE c.candidate_id = p_candidate_id;
$$;


--
-- Name: sp_market_discovery_get_latest_snapshot(character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_get_latest_snapshot(p_provider_code character varying, p_source_type character varying) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
SELECT jsonb_build_object(
    'snapshot_id', s.snapshot_id, 'provider_code', src.provider_code,
    'source_type', src.source_type, 'source_name', src.name,
    'observed_at', s.observed_at, 'requested_limit', s.requested_limit,
    'returned_count', s.returned_count, 'source_reference', s.source_reference,
    'source_metadata', s.source_metadata,
    'items', COALESCE((SELECT jsonb_agg(to_jsonb(i) - 'snapshot_id' - 'created_at' - 'updated_at'
        ORDER BY i.rank NULLS LAST, i.symbol) FROM public.market_discovery_snapshot_items i
        WHERE i.snapshot_id = s.snapshot_id), '[]'::jsonb)
)
FROM public.market_discovery_snapshots s
JOIN public.market_discovery_sources src ON src.source_id = s.source_id
WHERE src.provider_code = upper(btrim(p_provider_code))
  AND src.source_type = upper(btrim(p_source_type))
ORDER BY s.observed_at DESC, s.snapshot_id DESC LIMIT 1;
$$;


--
-- Name: sp_market_discovery_list_candidates(uuid, character varying, character varying, numeric, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_list_candidates(p_snapshot_id uuid DEFAULT NULL::uuid, p_resolution_status character varying DEFAULT NULL::character varying, p_origin character varying DEFAULT NULL::character varying, p_min_attention_score numeric DEFAULT 0, p_limit integer DEFAULT 100) RETURNS TABLE(candidate_id uuid, snapshot_id uuid, snapshot_item_id uuid, entity_id uuid, instrument_id uuid, symbol character varying, origin character varying, resolution_status character varying, attention_score numeric, universe_codes jsonb, attention_evidence jsonb, observed_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
WITH selected_snapshot AS (
    SELECT COALESCE(
        p_snapshot_id,
        (
            SELECT s.snapshot_id
            FROM public.market_discovery_snapshots s
            ORDER BY s.observed_at DESC, s.snapshot_id DESC
            LIMIT 1
        )
    ) AS snapshot_id
)
SELECT
    c.candidate_id,
    c.snapshot_id,
    c.snapshot_item_id,
    c.entity_id,
    c.instrument_id,
    c.symbol,
    c.origin,
    c.resolution_status,
    c.attention_score,
    c.universe_codes,
    c.attention_evidence,
    s.observed_at
FROM public.market_discovery_candidates c
JOIN selected_snapshot selected ON selected.snapshot_id = c.snapshot_id
JOIN public.market_discovery_snapshots s ON s.snapshot_id = c.snapshot_id
WHERE (p_resolution_status IS NULL OR c.resolution_status = upper(btrim(p_resolution_status)))
  AND (p_origin IS NULL OR c.origin = upper(btrim(p_origin)))
  AND c.attention_score >= COALESCE(p_min_attention_score, 0)
ORDER BY c.attention_score DESC, c.symbol
LIMIT LEAST(GREATEST(p_limit, 1), 1000);
$$;


--
-- Name: sp_market_discovery_list_scan_candidates(uuid, numeric, numeric, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_list_scan_candidates(p_snapshot_id uuid DEFAULT NULL::uuid, p_core_min_attention_score numeric DEFAULT 20, p_discovery_min_attention_score numeric DEFAULT 40, p_core_limit integer DEFAULT 100, p_discovery_limit integer DEFAULT 25) RETURNS TABLE(candidate_id uuid, snapshot_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange text, currency_code text, isin text, universe_id uuid, origin character varying, attention_score numeric, universe_codes jsonb, attention_evidence jsonb)
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


--
-- Name: sp_market_discovery_list_snapshot_history(character varying, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_list_snapshot_history(p_provider_code character varying, p_source_type character varying, p_limit integer DEFAULT 100) RETURNS TABLE(snapshot_id uuid, observed_at timestamp with time zone, requested_limit integer, returned_count integer, source_reference text, source_metadata jsonb)
    LANGUAGE sql STABLE
    AS $$
SELECT s.snapshot_id, s.observed_at, s.requested_limit, s.returned_count,
       s.source_reference, s.source_metadata
FROM public.market_discovery_snapshots s
JOIN public.market_discovery_sources src ON src.source_id = s.source_id
WHERE src.provider_code = upper(btrim(p_provider_code))
  AND src.source_type = upper(btrim(p_source_type))
ORDER BY s.observed_at DESC, s.snapshot_id DESC
LIMIT least(greatest(p_limit, 1), 1000);
$$;


--
-- Name: sp_market_discovery_upsert_source(character varying, character varying, character varying, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_discovery_upsert_source(p_provider_code character varying, p_source_type character varying, p_name character varying, p_is_enabled boolean DEFAULT true) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE v_source_id uuid;
BEGIN
    IF NULLIF(btrim(p_provider_code), '') IS NULL OR NULLIF(btrim(p_source_type), '') IS NULL THEN
        RAISE EXCEPTION 'provider_code and source_type are required';
    END IF;
    INSERT INTO public.market_discovery_sources(provider_code, source_type, name, is_enabled)
    VALUES (upper(btrim(p_provider_code)), upper(btrim(p_source_type)), btrim(p_name), p_is_enabled)
    ON CONFLICT (provider_code, source_type) DO UPDATE SET
        name = EXCLUDED.name,
        is_enabled = EXCLUDED.is_enabled
    RETURNING source_id INTO v_source_id;
    RETURN v_source_id;
END;
$$;


--
-- Name: sp_market_screener_apply_consensus(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_apply_consensus(p_opportunities jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer := 0;
BEGIN
  UPDATE public.market_screener_opportunities o SET
    strategy_count=coalesce(nullif(x.v->>'strategy_count','')::integer,1),
    bullish_strategy_count=coalesce(nullif(x.v->>'bullish_strategy_count','')::integer,0),
    bearish_strategy_count=coalesce(nullif(x.v->>'bearish_strategy_count','')::integer,0),
    consensus_direction=x.v->>'consensus_direction',
    consensus_score=nullif(x.v->>'consensus_score','')::numeric,
    conflict_score=nullif(x.v->>'conflict_score','')::numeric,
    supporting_strategies=coalesce(x.v->'supporting_strategies','[]'::jsonb),
    opposing_strategies=coalesce(x.v->'opposing_strategies','[]'::jsonb),
    supporting_signal_ids=coalesce(x.v->'supporting_signal_ids','[]'::jsonb)
  FROM jsonb_array_elements(coalesce(p_opportunities,'[]'::jsonb)) x(v)
  WHERE o.signal_id=(x.v->>'signal_id')::uuid;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN jsonb_build_object('updated',v_count);
END $$;


--
-- Name: sp_market_screener_complete_run(uuid, character varying, uuid, uuid, jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_complete_run(p_screener_run_id uuid, p_status character varying, p_discovery_snapshot_id uuid, p_scan_run_id uuid, p_phase_results jsonb, p_error_message text DEFAULT NULL::text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_status NOT IN ('SUCCESS', 'PARTIAL', 'FAILED') THEN
        RAISE EXCEPTION 'Invalid terminal status: %', p_status;
    END IF;
    UPDATE public.market_screener_runs
    SET status = p_status,
        discovery_snapshot_id = p_discovery_snapshot_id,
        scan_run_id = p_scan_run_id,
        phase_results = COALESCE(p_phase_results, '{}'::jsonb),
        error_message = p_error_message,
        completed_at = now()
    WHERE screener_run_id = p_screener_run_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Screener run not found: %', p_screener_run_id; END IF;
END;
$$;


--
-- Name: sp_market_screener_create_ranking_snapshot(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_create_ranking_snapshot(p_run_id uuid) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_snapshot_id uuid;
    v_opportunity_count integer;
BEGIN
    SELECT count(*)::integer
      INTO v_opportunity_count
      FROM public.market_screener_opportunities o
     WHERE o.active = true;

    INSERT INTO public.market_screener_ranking_snapshots (
        screener_run_id,
        opportunity_count
    )
    VALUES (
        p_run_id,
        coalesce(v_opportunity_count, 0)
    )
    RETURNING ranking_snapshot_id
         INTO v_snapshot_id;

    INSERT INTO public.market_screener_ranking_snapshot_items (
        ranking_snapshot_id,
        opportunity_id,
        signal_id,
        entity_id,
        instrument_id,
        symbol,
        direction,
        origin,
        ranking_position,
        attention_score,
        technical_score,
        quant_score,
        llm_score,
        composite_score,
        verdict,
        summary,
        evidence,
        strategy_code,
        signal_type,
        signal_timeframe,
        signal_trend,
        signal_confidence,
        reference_price,
        invalidation_price,
        target_price,
        signal_reasons,
        technical_evidence,
        strategy_count,
        bullish_strategy_count,
        bearish_strategy_count,
        consensus_direction,
        consensus_score,
        conflict_score,
        supporting_strategies,
        opposing_strategies
    )
    SELECT
        v_snapshot_id,
        o.opportunity_id,
        o.signal_id,
        o.entity_id,
        o.instrument_id,
        o.symbol,
        o.direction,
        coalesce(o.origin, 'MARKET_ANALYSIS'),
        o.ranking_position,
        o.attention_score,
        o.technical_score,
        o.quant_score,
        o.llm_score,
        o.composite_score,
        o.verdict,
        o.summary,
        coalesce(o.evidence, '{}'::jsonb),
        o.strategy_code,
        o.signal_type,
        o.signal_timeframe,
        o.signal_trend,
        o.signal_confidence,
        o.reference_price,
        o.invalidation_price,
        o.target_price,
        coalesce(o.signal_reasons, '[]'::jsonb),
        coalesce(o.technical_evidence, '{}'::jsonb),
        coalesce(o.strategy_count, 1),
        coalesce(o.bullish_strategy_count, 0),
        coalesce(o.bearish_strategy_count, 0),
        coalesce(o.consensus_direction, 'NEUTRAL'),
        coalesce(o.consensus_score, 0),
        coalesce(o.conflict_score, 0),
        coalesce(o.supporting_strategies, '[]'::jsonb),
        coalesce(o.opposing_strategies, '[]'::jsonb)
    FROM public.market_screener_opportunities o
    WHERE o.active = true
    ORDER BY o.ranking_position, o.opportunity_id;

    RETURN v_snapshot_id;
END;
$$;


--
-- Name: sp_market_screener_enrich_consensus_payload(jsonb, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_enrich_consensus_payload(p_payload jsonb, p_opportunity_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$ SELECT CASE WHEN p_payload IS NULL THEN NULL ELSE p_payload || jsonb_build_object('strategy_count',o.strategy_count,'bullish_strategy_count',o.bullish_strategy_count,'bearish_strategy_count',o.bearish_strategy_count,'consensus_direction',o.consensus_direction,'consensus_score',o.consensus_score,'conflict_score',o.conflict_score,'supporting_strategies',o.supporting_strategies,'opposing_strategies',o.opposing_strategies,'supporting_signal_ids',o.supporting_signal_ids) END FROM public.market_screener_opportunities o WHERE o.opportunity_id=p_opportunity_id $$;


--
-- Name: sp_market_screener_enrich_opportunity_payload(jsonb, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_enrich_opportunity_payload(p_payload jsonb, p_opportunity_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN p_payload IS NULL THEN NULL
        ELSE p_payload || jsonb_build_object(
            'entity_id', entity.entity_id,
            'entity_name', entity.name,
            'entity_type', entity.entity_type,
            'entity_country_code', entity.country_code,
            'source_instrument_id', instrument.instrument_id,
            'source_symbol', instrument.symbol,
            'source_exchange', instrument.exchange,
            'source_currency_code', instrument.currency_code,
            'source_isin', NULL,
            'strategy_code', opportunity.strategy_code,
            'signal_type', opportunity.signal_type,
            'signal_timeframe', opportunity.signal_timeframe,
            'signal_trend', opportunity.signal_trend,
            'signal_confidence', opportunity.signal_confidence,
            'reference_price', opportunity.reference_price,
            'invalidation_price', opportunity.invalidation_price,
            'target_price', opportunity.target_price,
            'signal_reasons', coalesce(opportunity.signal_reasons, '[]'::jsonb),
            'technical_evidence', coalesce(opportunity.technical_evidence, '{}'::jsonb),
            'portfolio_match', coalesce((p_payload ->> 'portfolio_match')::boolean, false),
            'portfolio_match_type', coalesce(
                p_payload ->> 'portfolio_match_type',
                'NO_MATCH'
            ),
            'portfolio_position_count', coalesce(
                (p_payload ->> 'portfolio_position_count')::integer,
                0
            ),
            'portfolio_symbols', coalesce(
                p_payload -> 'portfolio_symbols',
                '[]'::jsonb
            ),
            'portfolio_names', coalesce(
                p_payload -> 'portfolio_names',
                '[]'::jsonb
            ),
            'portfolio_positions', coalesce(
                p_payload -> 'portfolio_positions',
                '[]'::jsonb
            ),
            'alternative_instruments', coalesce(
                alternatives.items,
                '[]'::jsonb
            )
        )
    END
    FROM public.market_screener_opportunities opportunity
    JOIN public.instruments instrument
      ON instrument.instrument_id = opportunity.instrument_id
    JOIN public.entities entity
      ON entity.entity_id = coalesce(
          opportunity.entity_id,
          instrument.entity_id
      )
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'instrument_id', related.instrument_id,
                'symbol', related.symbol,
                'exchange', related.exchange,
                'currency_code', related.currency_code,
                'isin', NULL,
                'is_source', related.instrument_id = instrument.instrument_id,
                'same_isin', false
            )
            ORDER BY
                (related.instrument_id = instrument.instrument_id) DESC,
                related.symbol,
                related.exchange
        ) AS items
        FROM public.instruments related
        WHERE related.entity_id = entity.entity_id
    ) alternatives ON true
    WHERE opportunity.opportunity_id = p_opportunity_id;
$$;


--
-- Name: sp_market_screener_enrich_snapshot_consensus_payload(jsonb, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_enrich_snapshot_consensus_payload(p_payload jsonb, p_item_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$ SELECT CASE WHEN p_payload IS NULL THEN NULL ELSE p_payload || jsonb_build_object('strategy_count',i.strategy_count,'bullish_strategy_count',i.bullish_strategy_count,'bearish_strategy_count',i.bearish_strategy_count,'consensus_direction',i.consensus_direction,'consensus_score',i.consensus_score,'conflict_score',i.conflict_score,'supporting_strategies',i.supporting_strategies,'opposing_strategies',i.opposing_strategies) END FROM public.market_screener_ranking_snapshot_items i WHERE i.ranking_snapshot_item_id=p_item_id $$;


--
-- Name: sp_market_screener_get_opportunity(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_get_opportunity(p_opportunity_id uuid) RETURNS TABLE(opportunity_id uuid, signal_id uuid, instrument_id uuid, ranking_position integer, symbol text, direction text, origin text, attention_score numeric, technical_score numeric, quant_score numeric, llm_score numeric, composite_score numeric, verdict text, summary text, evidence jsonb, active boolean, created_at timestamp with time zone, updated_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        o.opportunity_id,
        o.signal_id,
        o.instrument_id,
        o.ranking_position,
        o.symbol::text,
        o.direction::text,
        o.origin::text,
        o.attention_score,
        o.technical_score,
        o.quant_score,
        o.llm_score,
        o.composite_score,
        o.verdict::text,
        o.summary,
        o.evidence,
        o.active,
        o.created_at,
        o.updated_at
    FROM public.market_screener_opportunities AS o
    WHERE o.opportunity_id = p_opportunity_id;
$$;


--
-- Name: sp_market_screener_get_user_opportunity(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_get_user_opportunity(p_user_id uuid, p_opportunity_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_payload jsonb;
BEGIN
    IF p_user_id IS NULL OR p_opportunity_id IS NULL THEN
        RAISE EXCEPTION 'p_user_id and p_opportunity_id are required';
    END IF;

    SELECT item
      INTO v_payload
      FROM public.sp_market_screener_list_user_opportunities(
          p_user_id,
          NULL::character varying,
          NULL::character varying,
          NULL::character varying,
          NULL::boolean,
          0::numeric,
          NULL::text,
          1000::integer
      ) item
     WHERE (item ->> 'opportunity_id')::uuid = p_opportunity_id
     LIMIT 1;

    RETURN v_payload;
END;
$$;


--
-- Name: sp_market_screener_list_alert_candidates(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_alert_candidates(p_limit integer) RETURNS SETOF jsonb
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


--
-- Name: sp_market_screener_list_opportunities(character varying, character varying, character varying, numeric, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_opportunities(p_origin character varying DEFAULT NULL::character varying, p_direction character varying DEFAULT NULL::character varying, p_verdict character varying DEFAULT NULL::character varying, p_minimum_score numeric DEFAULT 0, p_query character varying DEFAULT NULL::character varying, p_limit integer DEFAULT 100) RETURNS TABLE(opportunity_id uuid, signal_id uuid, instrument_id uuid, ranking_position integer, symbol text, direction text, origin text, attention_score numeric, technical_score numeric, quant_score numeric, llm_score numeric, composite_score numeric, verdict text, summary text, evidence jsonb, updated_at timestamp with time zone)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        o.opportunity_id,
        o.signal_id,
        o.instrument_id,
        o.ranking_position,
        o.symbol::text,
        o.direction::text,
        o.origin::text,
        o.attention_score,
        o.technical_score,
        o.quant_score,
        o.llm_score,
        o.composite_score,
        o.verdict::text,
        o.summary,
        o.evidence,
        o.updated_at
    FROM public.market_screener_opportunities AS o
    WHERE o.active = true
      AND (p_origin IS NULL OR o.origin = p_origin)
      AND (p_direction IS NULL OR o.direction = p_direction)
      AND (p_verdict IS NULL OR o.verdict = p_verdict)
      AND o.composite_score >= COALESCE(p_minimum_score, 0)
      AND (
          p_query IS NULL
          OR BTRIM(p_query) = ''
          OR o.symbol ILIKE '%' || BTRIM(p_query) || '%'
          OR COALESCE(o.summary, '') ILIKE '%' || BTRIM(p_query) || '%'
      )
    ORDER BY
        o.ranking_position NULLS LAST,
        o.symbol
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 100), 1), 1000);
$$;


--
-- Name: sp_market_screener_list_ranking_inputs(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_ranking_inputs(p_limit integer) RETURNS TABLE(signal_id uuid, instrument_id uuid, symbol character varying, direction character varying, origin character varying, attention_score numeric, technical_score numeric, quant_score numeric, llm_score numeric, verdict character varying, summary text, evidence jsonb, strategy_code character varying, timeframe character varying)
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


--
-- Name: sp_market_screener_list_ranking_snapshots(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_ranking_snapshots(p_limit integer DEFAULT 50) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'ranking_snapshot_id', snapshot.ranking_snapshot_id,
        'screener_run_id', snapshot.screener_run_id,
        'observed_at', snapshot.observed_at,
        'opportunity_count', snapshot.opportunity_count
    )
    FROM public.market_screener_ranking_snapshots AS snapshot
    ORDER BY snapshot.observed_at DESC
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 50), 1), 500);
$$;


--
-- Name: sp_market_screener_list_runs(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_runs(p_limit integer DEFAULT 50) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'screener_run_id', run.screener_run_id,
        'status', run.status,
        'discovery_snapshot_id', run.discovery_snapshot_id,
        'scan_run_id', run.scan_run_id,
        'started_at', run.started_at,
        'completed_at', run.completed_at,
        'phase_results', run.phase_results,
        'error_message', run.error_message,
        'ranking_snapshot_id', snapshot.ranking_snapshot_id,
        'opportunity_count', COALESCE(snapshot.opportunity_count, 0)
    )
    FROM public.market_screener_runs AS run
    LEFT JOIN public.market_screener_ranking_snapshots AS snapshot
      ON snapshot.screener_run_id = run.screener_run_id
    ORDER BY run.started_at DESC
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 50), 1), 500);
$$;


--
-- Name: sp_market_screener_list_snapshot_items(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_snapshot_items(p_ranking_snapshot_id uuid) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'ranking_snapshot_item_id', item.ranking_snapshot_item_id,
        'ranking_snapshot_id', item.ranking_snapshot_id,
        'opportunity_id', item.opportunity_id,
        'signal_id', item.signal_id,
        'entity_id', item.entity_id,
        'instrument_id', item.instrument_id,
        'symbol', item.symbol,
        'direction', item.direction,
        'origin', item.origin,
        'ranking_position', item.ranking_position,
        'previous_ranking_position', item.previous_ranking_position,
        'ranking_change', item.ranking_change,
        'attention_score', item.attention_score,
        'technical_score', item.technical_score,
        'quant_score', item.quant_score,
        'llm_score', item.llm_score,
        'composite_score', item.composite_score,
        'verdict', item.verdict,
        'summary', item.summary,
        'evidence', item.evidence
    )
    FROM public.market_screener_ranking_snapshot_items item
    WHERE item.ranking_snapshot_id = p_ranking_snapshot_id
    ORDER BY item.ranking_position;
$$;


--
-- Name: sp_market_screener_list_user_opportunities(uuid, character varying, character varying, character varying, boolean, numeric, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_user_opportunities(p_user_id uuid, p_origin character varying, p_direction character varying, p_verdict character varying, p_portfolio_match boolean, p_minimum_score numeric, p_query text, p_limit integer) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT
        to_jsonb(opportunity)
        || jsonb_build_object(
            'symbol', source_instrument.symbol,
            'portfolio_match', portfolio_match.position_count > 0,
            'portfolio_match_type', coalesce(portfolio_match.match_type, 'NO_MATCH'),
            'portfolio_position_count', portfolio_match.position_count,
            'portfolio_symbols', portfolio_match.symbols,
            'portfolio_names', portfolio_match.portfolio_names,
            'portfolio_positions', portfolio_match.positions
        )
    FROM public.market_screener_opportunities opportunity
    JOIN public.instruments source_instrument
      ON source_instrument.instrument_id = opportunity.instrument_id
    LEFT JOIN public.entities source_entity
      ON source_entity.entity_id = coalesce(
          opportunity.entity_id,
          source_instrument.entity_id
      )
    LEFT JOIN LATERAL (
        SELECT
            count(*)::integer AS position_count,
            CASE
                WHEN bool_or(position.instrument_id = opportunity.instrument_id)
                    THEN 'EXACT_INSTRUMENT'
                WHEN count(*) > 0
                    THEN 'SAME_ENTITY'
                ELSE NULL
            END AS match_type,
            coalesce(
                jsonb_agg(DISTINCT portfolio_instrument.symbol)
                    FILTER (WHERE portfolio_instrument.symbol IS NOT NULL),
                '[]'::jsonb
            ) AS symbols,
            coalesce(
                jsonb_agg(DISTINCT portfolio.name)
                    FILTER (WHERE portfolio.name IS NOT NULL),
                '[]'::jsonb
            ) AS portfolio_names,
            coalesce(
                jsonb_agg(
                    jsonb_build_object(
                        'position_id', position.position_id,
                        'portfolio_id', portfolio.portfolio_id,
                        'portfolio_name', portfolio.name,
                        'instrument_id', portfolio_instrument.instrument_id,
                        'symbol', portfolio_instrument.symbol,
                        'exchange', portfolio_instrument.exchange,
                        'currency_code', portfolio_instrument.currency_code,
                        'isin', NULL,
                        'quantity', position.quantity,
                        'match_type', CASE
                            WHEN position.instrument_id = opportunity.instrument_id
                                THEN 'EXACT_INSTRUMENT'
                            ELSE 'SAME_ENTITY'
                        END
                    )
                    ORDER BY portfolio.name, portfolio_instrument.symbol, position.position_id
                ),
                '[]'::jsonb
            ) AS positions
        FROM public.positions position
        JOIN public.portfolios portfolio
          ON portfolio.portfolio_id = position.portfolio_id
         AND portfolio.user_id = p_user_id
        JOIN public.instruments portfolio_instrument
          ON portfolio_instrument.instrument_id = position.instrument_id
        WHERE
            position.instrument_id = opportunity.instrument_id
            OR portfolio_instrument.entity_id = coalesce(
                opportunity.entity_id,
                source_instrument.entity_id
            )
    ) portfolio_match ON true
    WHERE (p_origin IS NULL OR opportunity.origin = p_origin)
      AND (p_direction IS NULL OR opportunity.direction = p_direction)
      AND (p_verdict IS NULL OR opportunity.verdict = p_verdict)
      AND opportunity.composite_score >= coalesce(p_minimum_score, 0)
      AND (
          p_query IS NULL
          OR source_instrument.symbol ILIKE '%' || p_query || '%'
          OR coalesce(source_entity.name, '') ILIKE '%' || p_query || '%'
          OR coalesce(opportunity.summary, '') ILIKE '%' || p_query || '%'
          OR EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(portfolio_match.symbols)
                  AS owned_symbol(symbol_value)
              WHERE owned_symbol.symbol_value ILIKE '%' || p_query || '%'
          )
      )
      AND (
          p_portfolio_match IS NULL
          OR (portfolio_match.position_count > 0) = p_portfolio_match
      )
    ORDER BY
        opportunity.ranking_position NULLS LAST,
        opportunity.composite_score DESC
    LIMIT greatest(coalesce(p_limit, 100), 1);
$$;


--
-- Name: sp_market_screener_list_user_signals(uuid, character varying, character varying, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_list_user_signals(p_user_id uuid, p_signal_type character varying DEFAULT NULL::character varying, p_strategy_code character varying DEFAULT NULL::character varying, p_direction character varying DEFAULT NULL::character varying, p_limit integer DEFAULT 100) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT public.sp_market_screener_enrich_opportunity_payload(
        payload,
        (payload->>'opportunity_id')::uuid
    )
    FROM public.sp_market_screener_list_user_opportunities(
        p_user_id,
        NULL,
        p_direction,
        NULL,
        NULL,
        0,
        NULL,
        LEAST(GREATEST(COALESCE(p_limit, 100), 1), 1000)
    ) AS payload
    JOIN public.market_screener_opportunities AS opportunity
      ON opportunity.opportunity_id =
         (payload->>'opportunity_id')::uuid
    WHERE (
        p_signal_type IS NULL
        OR opportunity.signal_type = p_signal_type
    )
      AND (
        p_strategy_code IS NULL
        OR opportunity.strategy_code = p_strategy_code
    )
    ORDER BY opportunity.ranking_position NULLS LAST;
$$;


--
-- Name: sp_market_screener_replace_ranking(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_replace_ranking(p_opportunities jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_count integer := 0;
BEGIN
    DELETE FROM public.market_screener_opportunities;

    INSERT INTO public.market_screener_opportunities (
        signal_id,
        instrument_id,
        symbol,
        direction,
        origin,
        attention_score,
        technical_score,
        quant_score,
        llm_score,
        composite_score,
        ranking_position,
        verdict,
        summary,
        evidence
    )
    SELECT
        (x ->> 'signal_id')::uuid,
        (x ->> 'instrument_id')::uuid,
        x ->> 'symbol',
        x ->> 'direction',
        x ->> 'origin',
        nullif(x ->> 'attention_score', '')::numeric,
        nullif(x ->> 'technical_score', '')::numeric,
        nullif(x ->> 'quant_score', '')::numeric,
        nullif(x ->> 'llm_score', '')::numeric,
        nullif(x ->> 'composite_score', '')::numeric,
        nullif(x ->> 'ranking_position', '')::integer,
        x ->> 'verdict',
        x ->> 'summary',
        coalesce(x -> 'evidence', '{}'::jsonb)
    FROM jsonb_array_elements(coalesce(p_opportunities, '[]'::jsonb)) x;

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN jsonb_build_object('replaced', v_count);
END;
$$;


--
-- Name: sp_market_screener_start_run(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_market_screener_start_run() RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.market_screener_runs(status,phase_results)
  VALUES ('RUNNING','{}'::jsonb) RETURNING screener_run_id INTO v_id;
  RETURN v_id;
END $$;


--
-- Name: sp_migrate_exchange_sync_job(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_migrate_exchange_sync_job() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_updated integer;
BEGIN
    LOCK TABLE public.scheduler_jobs IN SHARE ROW EXCLUSIVE MODE;

    IF EXISTS (
        SELECT 1 FROM public.scheduler_jobs
        WHERE job_key = 'eulerpool_exchange_holidays'
    ) AND EXISTS (
        SELECT 1 FROM public.scheduler_jobs
        WHERE job_key = 'eulerpool_exchange_sync'
    ) THEN
        RAISE EXCEPTION 'Both legacy and exchange-sync scheduler jobs exist; explicit reconciliation required';
    END IF;

    UPDATE public.scheduler_jobs
    SET job_key = 'eulerpool_exchange_sync',
        job_name = 'Eulerpool Exchange Sync',
        python_module = 'jobs.eulerpool_exchange_sync',
        updated_at = now()
    WHERE job_key = 'eulerpool_exchange_holidays';

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;


--
-- Name: sp_news_get_user_news(uuid, character varying, timestamp with time zone, timestamp with time zone, character varying, numeric, numeric, numeric, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_get_user_news(p_user_id uuid, p_scope character varying DEFAULT 'MY_ASSETS'::character varying, p_date_from timestamp with time zone DEFAULT NULL::timestamp with time zone, p_date_to timestamp with time zone DEFAULT NULL::timestamp with time zone, p_source character varying DEFAULT NULL::character varying, p_sentiment_min numeric DEFAULT NULL::numeric, p_sentiment_max numeric DEFAULT NULL::numeric, p_impact_min numeric DEFAULT NULL::numeric, p_query character varying DEFAULT NULL::character varying, p_limit integer DEFAULT 500) RETURNS TABLE(news_id uuid, cluster_id uuid, title text, summary text, url text, published_at timestamp with time zone, source character varying, entities jsonb, symbols jsonb, relevance numeric, sentiment numeric, impact numeric, direct_impact numeric, propagated_impact numeric, confidence numeric, in_portfolio boolean, in_watchlist boolean, asset_scope character varying, raw_json jsonb)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_scope varchar;
    v_query varchar;
BEGIN
    v_scope := upper(coalesce(nullif(btrim(p_scope), ''), 'MY_ASSETS'));
    v_query := nullif(btrim(p_query), '');

    IF v_scope NOT IN ('MY_ASSETS', 'PORTFOLIO', 'WATCHLIST', 'ALL') THEN
        RAISE EXCEPTION 'Unsupported news scope: %', p_scope;
    END IF;
    IF p_limit < 1 OR p_limit > 2000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 2000';
    END IF;
    IF p_sentiment_min IS NOT NULL
       AND p_sentiment_max IS NOT NULL
       AND p_sentiment_min > p_sentiment_max THEN
        RAISE EXCEPTION 'sentiment_min cannot exceed sentiment_max';
    END IF;

    RETURN QUERY
    WITH news_entity_rows AS (
        SELECT
            n.news_id,
            n.cluster_id,
            n.title,
            n.summary,
            n.url,
            n.published_at,
            ns.name::varchar AS source,
            n.raw_json,
            e.entity_id,
            e.name AS entity_name,
            ne.relevance,
            ne.sentiment,
            ne.impact,
            ni.direct_impact,
            ni.propagated_impact,
            ni.confidence,
            public.sp_get_user_entity_asset_match(
                p_user_id,
                e.entity_id,
                NULL
            ) AS asset_match
        FROM public.news n
        LEFT JOIN public.news_sources ns
          ON ns.source_id = n.source_id
        LEFT JOIN public.news_entities ne
          ON ne.news_id = n.news_id
        LEFT JOIN public.entities e
          ON e.entity_id = ne.entity_id
        LEFT JOIN public.news_impacts ni
          ON ni.news_id = n.news_id
         AND ni.entity_id = ne.entity_id
    ), news_rollup AS (
        SELECT
            row.news_id,
            row.cluster_id,
            row.title,
            row.summary,
            row.url,
            row.published_at,
            row.source,
            coalesce(
                jsonb_agg(DISTINCT jsonb_build_object(
                    'entity_id', row.entity_id,
                    'name', row.entity_name,
                    'relevance', row.relevance,
                    'sentiment', row.sentiment,
                    'impact', row.impact
                )) FILTER (WHERE row.entity_id IS NOT NULL),
                '[]'::jsonb
            ) AS entities,
            coalesce((
                SELECT jsonb_agg(DISTINCT jsonb_build_object(
                    'symbol', instrument.symbol,
                    'exchange', instrument.exchange
                ))
                FROM public.instruments instrument
                WHERE instrument.entity_id IN (
                    SELECT entity_row.entity_id
                    FROM news_entity_rows entity_row
                    WHERE entity_row.news_id = row.news_id
                      AND entity_row.entity_id IS NOT NULL
                )
            ), '[]'::jsonb) AS symbols,
            max(row.relevance) AS relevance,
            CASE
                WHEN count(row.sentiment) > 0 THEN avg(row.sentiment)
            END AS sentiment,
            max(row.impact) AS impact,
            max(row.direct_impact) AS direct_impact,
            max(row.propagated_impact) AS propagated_impact,
            max(row.confidence) AS confidence,
            bool_or(coalesce((row.asset_match ->> 'in_portfolio')::boolean, false))
                AS in_portfolio,
            bool_or(coalesce((row.asset_match ->> 'in_watchlist')::boolean, false))
                AS in_watchlist,
            coalesce(
                jsonb_agg(DISTINCT row.asset_match)
                    FILTER (WHERE row.entity_id IS NOT NULL),
                '[]'::jsonb
            ) AS asset_matches,
            row.raw_json
        FROM news_entity_rows row
        GROUP BY
            row.news_id,
            row.cluster_id,
            row.title,
            row.summary,
            row.url,
            row.published_at,
            row.source,
            row.raw_json
    )
    SELECT
        rollup.news_id,
        rollup.cluster_id,
        rollup.title,
        rollup.summary,
        rollup.url,
        rollup.published_at,
        rollup.source,
        rollup.entities,
        rollup.symbols,
        rollup.relevance,
        rollup.sentiment,
        rollup.impact,
        rollup.direct_impact,
        rollup.propagated_impact,
        rollup.confidence,
        rollup.in_portfolio,
        rollup.in_watchlist,
        CASE
            WHEN rollup.in_portfolio AND rollup.in_watchlist THEN 'BOTH'
            WHEN rollup.in_portfolio THEN 'PORTFOLIO'
            WHEN rollup.in_watchlist THEN 'WATCHLIST'
            ELSE 'GLOBAL'
        END::varchar AS asset_scope,
        coalesce(rollup.raw_json, '{}'::jsonb) || jsonb_build_object(
            'asset_matches', rollup.asset_matches
        ) AS raw_json
    FROM news_rollup rollup
    WHERE (p_date_from IS NULL OR rollup.published_at >= p_date_from)
      AND (p_date_to IS NULL OR rollup.published_at <= p_date_to)
      AND (
          nullif(btrim(p_source), '') IS NULL
          OR upper(rollup.source) = upper(btrim(p_source))
      )
      AND (p_sentiment_min IS NULL OR rollup.sentiment >= p_sentiment_min)
      AND (p_sentiment_max IS NULL OR rollup.sentiment <= p_sentiment_max)
      AND (
          p_impact_min IS NULL
          OR greatest(
              coalesce(rollup.impact, 0),
              coalesce(rollup.direct_impact, 0),
              coalesce(rollup.propagated_impact, 0)
          ) >= p_impact_min
      )
      AND (
          v_query IS NULL
          OR rollup.title ILIKE '%' || v_query || '%'
          OR rollup.summary ILIKE '%' || v_query || '%'
          OR rollup.source ILIKE '%' || v_query || '%'
          OR rollup.entities::text ILIKE '%' || v_query || '%'
          OR rollup.symbols::text ILIKE '%' || v_query || '%'
          OR rollup.asset_matches::text ILIKE '%' || v_query || '%'
      )
      AND CASE v_scope
          WHEN 'MY_ASSETS' THEN rollup.in_portfolio OR rollup.in_watchlist
          WHEN 'PORTFOLIO' THEN rollup.in_portfolio
          WHEN 'WATCHLIST' THEN rollup.in_watchlist
          ELSE true
      END
    ORDER BY rollup.published_at DESC NULLS LAST, rollup.news_id
    LIMIT p_limit;
END;
$$;


--
-- Name: sp_news_get_user_news_facets(uuid, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_get_user_news_facets(p_user_id uuid, p_scope character varying DEFAULT 'MY_ASSETS'::character varying) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
WITH rows AS (
    SELECT * FROM public.sp_news_get_user_news(
        p_user_id, p_scope, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 2000
    )
)
SELECT jsonb_build_object(
    'sources', coalesce((
        SELECT jsonb_agg(source ORDER BY source)
        FROM (SELECT DISTINCT source FROM rows WHERE source IS NOT NULL) q
    ), '[]'::jsonb),
    'published_from', (SELECT min(published_at) FROM rows),
    'published_to', (SELECT max(published_at) FROM rows)
);
$$;


--
-- Name: sp_news_list_ingestion_targets(boolean, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_list_ingestion_targets(p_include_all boolean DEFAULT false, p_limit integer DEFAULT 500) RETURNS TABLE(entity_id uuid, instrument_id uuid, symbol character varying, exchange character varying, instrument_type character varying, currency_code character varying, isin character varying)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    IF p_limit < 1 OR p_limit > 10000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 10000';
    END IF;

    RETURN QUERY
    WITH wanted_entities AS (
        SELECT DISTINCT i.entity_id
        FROM public.positions p
        JOIN public.instruments i
          ON i.instrument_id = p.instrument_id
        WHERE i.entity_id IS NOT NULL

        UNION

        SELECT DISTINCT wi.entity_id
        FROM public.watchlist_items wi
        WHERE wi.entity_id IS NOT NULL
    ),
    ranked_instruments AS (
        SELECT
            i.entity_id,
            i.instrument_id,
            i.symbol::varchar AS symbol,
            i.exchange::varchar AS exchange,
            i.instrument_type::varchar AS instrument_type,
            i.currency_code::varchar AS currency_code,
            NULL::varchar AS isin,
            row_number() OVER (
                PARTITION BY i.entity_id
                ORDER BY
                    CASE
                        WHEN i.instrument_type::text IN ('STOCK', 'ETF')
                            THEN 0
                        ELSE 1
                    END,
                    i.updated_at DESC NULLS LAST,
                    i.instrument_id
            ) AS row_number
        FROM public.instruments i
        WHERE i.entity_id IS NOT NULL
          AND (
              p_include_all
              OR EXISTS (
                  SELECT 1
                  FROM wanted_entities we
                  WHERE we.entity_id = i.entity_id
              )
          )
    )
    SELECT
        ri.entity_id,
        ri.instrument_id,
        ri.symbol,
        ri.exchange,
        ri.instrument_type,
        ri.currency_code,
        ri.isin
    FROM ranked_instruments ri
    WHERE ri.row_number = 1
    ORDER BY ri.entity_id
    LIMIT p_limit;
END;
$$;


--
-- Name: sp_news_list_pending_entity_scores(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_list_pending_entity_scores(p_limit integer DEFAULT 200) RETURNS TABLE(news_entity_id uuid, news_id uuid, entity_id uuid, entity_name text, symbols jsonb, title text, summary text, source_name character varying, published_at timestamp with time zone)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    IF p_limit < 1 OR p_limit > 5000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 5000';
    END IF;

    RETURN QUERY
    SELECT
        ne.news_entity_id,
        ne.news_id,
        ne.entity_id,
        e.name::text AS entity_name,
        coalesce(
            jsonb_agg(
                DISTINCT jsonb_build_object(
                    'symbol', i.symbol,
                    'exchange', i.exchange
                )
            ) FILTER (WHERE i.instrument_id IS NOT NULL),
            '[]'::jsonb
        ) AS symbols,
        n.title,
        n.summary,
        ns.name AS source_name,
        n.published_at
    FROM public.news_entities ne
    JOIN public.news n ON n.news_id = ne.news_id
    JOIN public.entities e ON e.entity_id = ne.entity_id
    LEFT JOIN public.news_sources ns ON ns.source_id = n.source_id
    LEFT JOIN public.instruments i ON i.entity_id = ne.entity_id
    WHERE ne.relevance IS NULL
       OR ne.sentiment IS NULL
       OR ne.impact IS NULL
    GROUP BY
        ne.news_entity_id,
        ne.news_id,
        ne.entity_id,
        e.name,
        n.title,
        n.summary,
        ns.name,
        n.published_at
    ORDER BY n.published_at DESC NULLS LAST, ne.news_entity_id
    LIMIT p_limit;
END;
$$;


--
-- Name: sp_news_update_entity_scores(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_update_entity_scores(p_scores jsonb) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_updated integer;
BEGIN
    IF jsonb_typeof(p_scores) <> 'array' THEN
        RAISE EXCEPTION 'p_scores must be a JSON array';
    END IF;

    WITH parsed AS (
        SELECT
            (value->>'news_entity_id')::uuid AS news_entity_id,
            greatest(0.0, least(1.0, (value->>'relevance')::numeric)) AS relevance,
            greatest(-1.0, least(1.0, (value->>'sentiment')::numeric)) AS sentiment,
            greatest(0.0, least(1.0, (value->>'impact')::numeric)) AS impact
        FROM jsonb_array_elements(p_scores)
    )
    UPDATE public.news_entities ne
    SET
        relevance = parsed.relevance,
        sentiment = parsed.sentiment,
        impact = parsed.impact
    FROM parsed
    WHERE ne.news_entity_id = parsed.news_entity_id;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;


--
-- Name: sp_news_upsert_article(uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_news_upsert_article(p_entity_id uuid, p_payload jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source_id uuid;
    v_news_id uuid;
    v_content_hash varchar(64);
    v_source_name varchar(100);
    v_title text;
    v_summary text;
    v_url text;
    v_published_at timestamptz;
    v_news_inserted boolean := false;
    v_association_inserted boolean := false;
BEGIN
    IF p_entity_id IS NULL THEN
        RAISE EXCEPTION 'p_entity_id is required';
    END IF;

    IF p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object'
    THEN
        RAISE EXCEPTION 'p_payload must be a JSON object';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.entities e
        WHERE e.entity_id = p_entity_id
    ) THEN
        RAISE EXCEPTION 'Entity not found: %', p_entity_id;
    END IF;

    v_source_name := left(
        coalesce(
            nullif(btrim(p_payload->>'source_name'), ''),
            nullif(btrim(p_payload->>'publisher'), ''),
            nullif(btrim(p_payload->>'source'), ''),
            nullif(btrim(p_payload->>'provider'), ''),
            'UNKNOWN'
        ),
        100
    );

    v_title := coalesce(
        nullif(btrim(p_payload->>'headline'), ''),
        nullif(btrim(p_payload->>'title'), '')
    );

    v_summary := nullif(
        btrim(p_payload->>'summary'),
        ''
    );

    v_url := coalesce(
        nullif(btrim(p_payload->>'url'), ''),
        nullif(btrim(p_payload->>'link'), '')
    );

    v_published_at := coalesce(
        nullif(p_payload->>'published_at', '')::timestamptz,
        nullif(p_payload->>'publishedAt', '')::timestamptz,
        nullif(p_payload->>'date', '')::timestamptz
    );

    IF v_title IS NULL THEN
        RAISE EXCEPTION 'News title/headline is required';
    END IF;

    /*
     * Preferisce una chiave sorgente stabile quando presente.
     * In assenza di external_id, utilizza URL e contenuto normalizzato.
     */
    v_content_hash := encode(
        digest(
            concat_ws(
                '|',
                lower(v_source_name),
                coalesce(
                    nullif(btrim(p_payload->>'external_id'), ''),
                    nullif(btrim(p_payload->>'id'), ''),
                    ''
                ),
                coalesce(lower(v_url), ''),
                lower(v_title),
                coalesce(v_published_at::text, '')
            ),
            'sha256'
        ),
        'hex'
    );

    INSERT INTO public.news_sources (
        name
    )
    VALUES (
        v_source_name
    )
    ON CONFLICT (name)
    DO UPDATE SET
        name = EXCLUDED.name
    RETURNING source_id
    INTO v_source_id;

    INSERT INTO public.news (
        source_id,
        title,
        summary,
        url,
        content_hash,
        published_at,
        raw_json
    )
    VALUES (
        v_source_id,
        v_title,
        v_summary,
        v_url,
        v_content_hash,
        v_published_at,
        p_payload
    )
    ON CONFLICT (content_hash)
    DO NOTHING
    RETURNING news_id
    INTO v_news_id;

    IF v_news_id IS NOT NULL THEN
        v_news_inserted := true;
    ELSE
        SELECT n.news_id
        INTO v_news_id
        FROM public.news n
        WHERE n.content_hash = v_content_hash;
    END IF;

    IF v_news_id IS NULL THEN
        RAISE EXCEPTION
            'Unable to resolve news row for content hash %',
            v_content_hash;
    END IF;

    /*
     * Se la notizia esisteva già, aggiorna soltanto i dati integrativi
     * senza cancellare valori già presenti con valori nulli.
     */
    IF NOT v_news_inserted THEN
        UPDATE public.news n
        SET
            source_id = coalesce(n.source_id, v_source_id),
            title = coalesce(nullif(v_title, ''), n.title),
            summary = coalesce(v_summary, n.summary),
            url = coalesce(v_url, n.url),
            published_at = coalesce(v_published_at, n.published_at),
            raw_json =
                coalesce(n.raw_json, '{}'::jsonb)
                || coalesce(p_payload, '{}'::jsonb)
        WHERE n.news_id = v_news_id;
    END IF;

    INSERT INTO public.news_entities (
        news_id,
        entity_id,
        relevance,
        sentiment,
        impact
    )
    VALUES (
        v_news_id,
        p_entity_id,
        nullif(p_payload->>'relevance', '')::numeric,
        nullif(p_payload->>'sentiment', '')::numeric,
        nullif(p_payload->>'impact', '')::numeric
    )
    ON CONFLICT (news_id, entity_id)
    DO NOTHING;

    v_association_inserted := FOUND;

    RETURN jsonb_build_object(
        'news_id', v_news_id,
        'inserted', v_news_inserted,
        'updated', NOT v_news_inserted,
        'association_inserted', v_association_inserted,
        'associations_inserted',
            CASE
                WHEN v_association_inserted THEN 1
                ELSE 0
            END,
        'content_hash', v_content_hash
    );
END;
$$;


--
-- Name: sp_normalize_entity_name(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_normalize_entity_name(p_name text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$
    SELECT NULLIF(
        trim(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        lower(public.unaccent(coalesce(p_name, ''))),
                        '[^a-z0-9]+',
                        ' ',
                        'g'
                    ),
                    '\m(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|spa|sa|ag|nv|se)\M',
                    ' ',
                    'g'
                ),
                '\s+',
                ' ',
                'g'
            )
        ),
        ''
    );
$$;


--
-- Name: sp_normalize_listing_exchange(character varying, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_normalize_listing_exchange(p_symbol character varying, p_exchange character varying, p_system_code character varying DEFAULT NULL::character varying) RETURNS character varying
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_symbol varchar;
    v_exchange varchar;
    v_system_code varchar;
    v_mapped_exchange varchar;
    v_mapped_exchanges varchar[];
BEGIN
    v_symbol := upper(nullif(btrim(p_symbol), ''));
    v_exchange := upper(nullif(btrim(p_exchange), ''));
    v_system_code := upper(nullif(btrim(p_system_code), ''));

    IF v_exchange IS NOT NULL AND v_system_code IS NOT NULL THEN
        SELECT array_agg(DISTINCT esm.exchange)
        INTO v_mapped_exchanges
        FROM public.exchange_source_mappings esm
        JOIN public.external_systems es
          ON es.system_id = esm.system_id
        WHERE upper(btrim(es.code)) = v_system_code
          AND es.active
          AND (
              upper(btrim(esm.source_exchange_code)) = v_exchange
              OR upper(btrim(esm.source_exchange_name)) = v_exchange
          );

        IF cardinality(v_mapped_exchanges) > 1 THEN
            RAISE EXCEPTION 'Ambiguous exchange alias % for provider %',
                v_exchange, v_system_code;
        END IF;
        IF cardinality(v_mapped_exchanges) = 1 THEN
            RETURN v_mapped_exchanges[1];
        END IF;
    END IF;

    SELECT e.exchange INTO v_mapped_exchange
    FROM public.exchanges e
    WHERE upper(btrim(e.exchange)) = v_exchange;
    IF FOUND THEN
        RETURN v_mapped_exchange;
    END IF;

    IF v_symbol IS NOT NULL THEN
        v_mapped_exchange := CASE
            WHEN v_symbol LIKE '%.MI' THEN 'XMIL'
            WHEN v_symbol LIKE '%.DE' THEN 'XETR'
            WHEN v_symbol LIKE '%.SW' THEN 'XSWX'
            WHEN v_symbol LIKE '%.L'  THEN 'XLON'
            WHEN v_symbol LIKE '%.PA' THEN 'XPAR'
            WHEN v_symbol LIKE '%.AS' THEN 'XAMS'
            WHEN v_symbol LIKE '%.BR' THEN 'XBRU'
            WHEN v_symbol LIKE '%.MC' THEN 'XMAD'
            WHEN v_symbol LIKE '%.LS' THEN 'XLIS'
            WHEN v_symbol LIKE '%.VI' THEN 'XWBO'
            WHEN v_symbol LIKE '%.ST' THEN 'XSTO'
            WHEN v_symbol LIKE '%.CO' THEN 'XCSE'
            WHEN v_symbol LIKE '%.OL' THEN 'XOSL'
            WHEN v_symbol LIKE '%.HE' THEN 'XHEL'
            WHEN v_symbol LIKE '%.TO' THEN 'XTSE'
            WHEN v_symbol LIKE '%.V'  THEN 'XTSX'
            WHEN v_symbol LIKE '%.AX' THEN 'XASX'
            WHEN v_symbol LIKE '%.HK' THEN 'XHKG'
            WHEN v_symbol LIKE '%.T'  THEN 'XTKS'
            ELSE NULL
        END;

        IF v_mapped_exchange IS NOT NULL THEN
            RETURN v_mapped_exchange;
        END IF;
    END IF;

    RETURN v_exchange;
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
-- Name: sp_observe_entity_name(uuid, character varying, character varying, character varying, numeric, jsonb, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_observe_entity_name(p_entity_id uuid, p_system_code character varying, p_observed_name character varying, p_source_reference character varying DEFAULT NULL::character varying, p_confidence numeric DEFAULT NULL::numeric, p_raw_payload jsonb DEFAULT NULL::jsonb, p_make_canonical boolean DEFAULT false) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_system_id uuid;
    v_observation_id uuid;
    v_name varchar(255);
    v_entity_name varchar(255);
    v_name_locked boolean;
BEGIN
    v_name := NULLIF(btrim(p_observed_name), '');

    IF v_name IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT e.name, e.name_locked
    INTO v_entity_name, v_name_locked
    FROM public.entities e
    WHERE e.entity_id = p_entity_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entity not found: %', p_entity_id;
    END IF;

    SELECT es.system_id
    INTO v_system_id
    FROM public.external_systems es
    WHERE es.code = upper(btrim(p_system_code))
      AND es.active = true;

    IF v_system_id IS NULL THEN
        RAISE EXCEPTION 'Active external system not found: %', p_system_code;
    END IF;

    INSERT INTO public.entity_name_observations (
        entity_id,
        system_id,
        observed_name,
        source_reference,
        confidence,
        raw_payload,
        is_preferred
    )
    VALUES (
        p_entity_id,
        v_system_id,
        v_name,
        NULLIF(btrim(p_source_reference), ''),
        p_confidence,
        p_raw_payload,
        p_make_canonical AND NOT v_name_locked
    )
    ON CONFLICT (
        entity_id,
        system_id,
        observed_name,
        normalized_source_reference
    )
    DO UPDATE SET
        last_observed_at = now(),
        observation_count = entity_name_observations.observation_count + 1,
        confidence = COALESCE(
            EXCLUDED.confidence,
            entity_name_observations.confidence
        ),
        raw_payload = COALESCE(
            EXCLUDED.raw_payload,
            entity_name_observations.raw_payload
        ),
        is_preferred = entity_name_observations.is_preferred
            OR EXCLUDED.is_preferred
    RETURNING entity_name_observation_id
    INTO v_observation_id;

    IF p_make_canonical AND NOT v_name_locked THEN
        UPDATE public.entity_name_observations
        SET is_preferred = (entity_name_observation_id = v_observation_id)
        WHERE entity_id = p_entity_id;

        UPDATE public.entities
        SET
            name = v_name,
            canonical_name_observation_id = v_observation_id
        WHERE entity_id = p_entity_id;
    ELSIF v_entity_name IS NULL OR btrim(v_entity_name) = '' THEN
        UPDATE public.entities
        SET
            name = v_name,
            canonical_name_observation_id = v_observation_id
        WHERE entity_id = p_entity_id;
    END IF;

    RETURN v_observation_id;
END;
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
-- Name: sp_record_system_audit(uuid, character varying, character varying, character varying, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_record_system_audit(p_actor_user_id uuid, p_action character varying, p_resource_type character varying, p_resource_id character varying, p_new_value jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_audit_id uuid;
BEGIN
    INSERT INTO system_audit_log (
        actor_user_id,
        action,
        resource_type,
        resource_id,
        new_value
    )
    VALUES (
        p_actor_user_id,
        upper(trim(p_action)),
        upper(trim(p_resource_type)),
        p_resource_id,
        p_new_value
    )
    RETURNING audit_id
    INTO v_audit_id;

    RETURN v_audit_id;
END;
$$;


--
-- Name: sp_refresh_entity_from_profile(uuid, uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_refresh_entity_from_profile(p_entity_id uuid, p_instrument_id uuid, p_payload jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM 1
    FROM public.sp_upsert_asset_profile(
        p_payload,
        p_entity_id,
        p_instrument_id,
        false
    );
END;
$$;


--
-- Name: sp_register_exchange_alias(character varying, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_register_exchange_alias(p_exchange character varying, p_system_code character varying, p_alias character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_alias varchar;
    v_system_id uuid;
    v_system_count integer;
BEGIN
    v_alias := upper(nullif(btrim(p_alias), ''));
    IF v_alias IS NULL OR length(v_alias) > 100 THEN
        RAISE EXCEPTION 'Exchange alias must contain between 1 and 100 characters';
    END IF;
    PERFORM 1 FROM public.exchanges WHERE exchange = p_exchange FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Existing exchange row required: %', p_exchange;
    END IF;
    SELECT count(*) INTO v_system_count FROM public.external_systems
    WHERE upper(btrim(code)) = upper(btrim(p_system_code)) AND active;
    IF v_system_count <> 1 THEN
        RAISE EXCEPTION 'Expected one active external system for %', p_system_code;
    END IF;
    SELECT system_id INTO v_system_id FROM public.external_systems
    WHERE upper(btrim(code)) = upper(btrim(p_system_code)) AND active;

    LOCK TABLE public.exchange_source_mappings IN SHARE ROW EXCLUSIVE MODE;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE system_id = v_system_id AND exchange <> p_exchange
          AND (upper(btrim(source_exchange_code)) = v_alias
               OR upper(btrim(source_exchange_name)) = v_alias)
    ) THEN
        RAISE EXCEPTION 'Exchange alias % already belongs to another row for provider %',
            v_alias, p_system_code;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE system_id = v_system_id AND exchange = p_exchange
          AND (upper(btrim(source_exchange_code)) = v_alias
               OR upper(btrim(source_exchange_name)) = v_alias)
    ) THEN
        RETURN;
    END IF;
    INSERT INTO public.exchange_source_mappings
        (exchange, system_id, source_exchange_code, raw_payload)
    VALUES (p_exchange, v_system_id, v_alias,
            jsonb_build_object('registration', 'manual'));
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
-- Name: sp_release_stale_llm_requests(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_release_stale_llm_requests(p_timeout_seconds integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE public.llm_request_queue
    SET status = 'RETRY', locked_at = NULL, locked_by = NULL,
        available_at = now(),
        last_error = COALESCE(last_error, 'Recovered stale PROCESSING request')
    WHERE status = 'PROCESSING'
      AND locked_at < now() - make_interval(secs => p_timeout_seconds);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
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
-- Name: sp_renew_llm_request_lease(uuid, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_renew_llm_request_lease(p_request_id uuid, p_worker_id character varying, p_lease_seconds integer) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.llm_request_queue
    SET
        lease_expires_at =
            now() + make_interval(
                secs => GREATEST(p_lease_seconds, 1)
            )
    WHERE request_id = p_request_id
      AND status = 'PROCESSING'
      AND locked_by = p_worker_id;

    RETURN FOUND;
END;
$$;


--
-- Name: sp_resolve_entity_from_instrument_identities(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_resolve_entity_from_instrument_identities(p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_identifiers jsonb;
    v_entity_ids uuid[];
BEGIN
    v_identifiers := COALESCE(p_payload->'identifiers', '{}'::jsonb)
        || jsonb_strip_nulls(jsonb_build_object(
            'ISIN', p_payload->>'isin',
            'FIGI_SHARE_CLASS', p_payload->>'figi_share_class'
        ));

    SELECT array_agg(DISTINCT i.entity_id ORDER BY i.entity_id)
    INTO v_entity_ids
    FROM jsonb_each_text(v_identifiers) item
    JOIN public.identity_types it
      ON it.target_kind = 'INSTRUMENT'
     AND it.code = upper(item.key)
     AND it.code IN ('ISIN', 'FIGI_SHARE_CLASS')
    JOIN public.identities id
      ON id.identity_type_id = it.identity_type_id
     AND id.normalized_value = CASE
            WHEN it.case_sensitive THEN btrim(item.value)
            ELSE upper(btrim(item.value))
         END
    JOIN public.instrument_identities ii
      ON ii.identity_id = id.identity_id
    JOIN public.instruments i
      ON i.instrument_id = ii.instrument_id
    WHERE NULLIF(btrim(item.value), '') IS NOT NULL;

    IF COALESCE(array_length(v_entity_ids, 1), 0) = 0 THEN
        RETURN NULL;
    END IF;

    IF array_length(v_entity_ids, 1) > 1 THEN
        RAISE EXCEPTION
            'Strong identities resolve to multiple entities: %',
            v_entity_ids;
    END IF;

    RETURN v_entity_ids[1];
END;
$$;


--
-- Name: sp_resolve_entity_from_profile_identities(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_resolve_entity_from_profile_identities(p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_candidate_count integer;
    v_entity_id uuid;
    v_isin varchar;
    v_figi_share_class varchar;
BEGIN
    v_isin := upper(nullif(btrim(coalesce(
        p_payload->>'isin',
        p_payload->'identifiers'->>'ISIN',
        p_payload->'identifiers'->>'isin'
    )), ''));

    v_figi_share_class := upper(nullif(btrim(coalesce(
        p_payload->>'figi_share_class',
        p_payload->'identifiers'->>'FIGI_SHARE_CLASS',
        p_payload->'identifiers'->>'figi_share_class'
    )), ''));

    WITH candidates AS (
        SELECT DISTINCT i.entity_id
        FROM public.identities id
        JOIN public.identity_types it
          ON it.identity_type_id = id.identity_type_id
        JOIN public.instrument_identities ii
          ON ii.identity_id = id.identity_id
        JOIN public.instruments i
          ON i.instrument_id = ii.instrument_id
        WHERE it.target_kind = 'INSTRUMENT'
          AND (
              (it.code = 'ISIN' AND v_isin IS NOT NULL
               AND id.normalized_value = v_isin)
              OR
              (it.code = 'FIGI_SHARE_CLASS' AND v_figi_share_class IS NOT NULL
               AND id.normalized_value = v_figi_share_class)
          )
    )
    SELECT count(*), (array_agg(entity_id ORDER BY entity_id))[1]
    INTO v_candidate_count, v_entity_id
    FROM candidates;

    IF v_candidate_count > 1 THEN
        RAISE EXCEPTION
            'Profile strong identities resolve to % entities; consolidate them before importing',
            v_candidate_count;
    END IF;

    RETURN v_entity_id;
END;
$$;


--
-- Name: sp_resolve_user_notification_target(uuid, character varying, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_resolve_user_notification_target(p_user_id uuid, p_notification_type character varying, p_severity character varying, p_fallback_channel character varying DEFAULT 'TELEGRAM'::character varying) RETURNS TABLE(source_id uuid, channel character varying, resolution_status character varying)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_subscription public.notification_subscriptions%ROWTYPE;
    v_source public.user_sources%ROWTYPE;
    v_severity_rank integer;
    v_minimum_rank integer;
BEGIN
    SELECT ns.*
    INTO v_subscription
    FROM public.notification_subscriptions ns
    WHERE ns.user_id = p_user_id
      AND ns.portfolio_id IS NULL
      AND UPPER(ns.event_type) = UPPER(p_notification_type)
    LIMIT 1;

    IF FOUND THEN
        IF NOT v_subscription.enabled THEN
            RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
            RETURN;
        END IF;

        v_severity_rank := CASE UPPER(COALESCE(p_severity, 'LOW'))
            WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3
            WHEN 'MEDIUM' THEN 2 ELSE 1 END;
        v_minimum_rank := CASE UPPER(COALESCE(v_subscription.severity_min, 'LOW'))
            WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3
            WHEN 'MEDIUM' THEN 2 ELSE 1 END;

        IF v_severity_rank < v_minimum_rank THEN
            RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
            RETURN;
        END IF;

        SELECT us.*
        INTO v_source
        FROM public.user_sources us
        WHERE us.source_id = v_subscription.source_id
          AND us.user_id = p_user_id
          AND COALESCE((us.config->>'enabled')::boolean, true) = true;
    ELSE
        SELECT us.*
        INTO v_source
        FROM public.user_sources us
        WHERE us.user_id = p_user_id
          AND UPPER(us.source_type) = UPPER(p_fallback_channel)
          AND COALESCE((us.config->>'enabled')::boolean, true) = true
        ORDER BY us.created_at DESC
        LIMIT 1;
    END IF;

    IF v_source.source_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'SOURCE_NOT_FOUND'::varchar;
        RETURN;
    END IF;

    RETURN QUERY SELECT
        v_source.source_id,
        UPPER(v_source.source_type)::varchar,
        'RESOLVED'::varchar;
END;
$$;


--
-- Name: sp_scheduler_append_run_log(uuid, character varying, character varying, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_append_run_log(p_job_run_id uuid, p_level character varying, p_logger_name character varying, p_message text) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_log_id bigint;
BEGIN
    INSERT INTO public.scheduler_job_run_logs(
        job_run_id,
        level,
        logger_name,
        message
    )
    VALUES (
        p_job_run_id,
        upper(coalesce(nullif(trim(p_level), ''), 'INFO')),
        coalesce(nullif(trim(p_logger_name), ''), 'root'),
        p_message
    )
    RETURNING log_id
    INTO v_log_id;

    RETURN v_log_id;
END;
$$;


--
-- Name: sp_scheduler_claim_job_runs(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_claim_job_runs(p_limit integer DEFAULT 5) RETURNS TABLE(request_id uuid, job_run_id uuid, job_id uuid, job_key character varying, job_name character varying, python_module character varying)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT request.request_id
        FROM public.scheduler_manual_run_requests request
        WHERE request.status = 'PENDING'
        ORDER BY request.requested_at
        FOR UPDATE SKIP LOCKED
        LIMIT LEAST(GREATEST(p_limit, 1), 20)
    ), claimed AS (
        UPDATE public.scheduler_manual_run_requests request
        SET
            status = 'PROCESSING',
            claimed_at = now()
        FROM candidates candidate
        WHERE request.request_id = candidate.request_id
        RETURNING request.request_id, request.job_run_id, request.job_id
    )
    SELECT
        claimed.request_id,
        claimed.job_run_id,
        claimed.job_id,
        job.job_key,
        job.job_name,
        job.python_module
    FROM claimed
    JOIN public.scheduler_jobs job
      ON job.job_id = claimed.job_id;
END;
$$;


--
-- Name: sp_scheduler_complete_job_run_request(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_complete_job_run_request(p_job_run_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.scheduler_manual_run_requests
    SET
        status = 'COMPLETED',
        completed_at = now(),
        error_message = NULL
    WHERE job_run_id = p_job_run_id;

    RETURN FOUND;
END;
$$;


--
-- Name: sp_scheduler_defer_job_run_request(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_defer_job_run_request(p_job_run_id uuid) RETURNS void
    LANGUAGE sql
    AS $$
    UPDATE public.scheduler_manual_run_requests
    SET status = 'PENDING', claimed_at = NULL
    WHERE job_run_id = p_job_run_id AND status = 'PROCESSING';
$$;


--
-- Name: sp_scheduler_fail_job_run_request(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_fail_job_run_request(p_job_run_id uuid, p_error_message text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.scheduler_manual_run_requests
    SET
        status = 'FAILED',
        completed_at = now(),
        error_message = p_error_message
    WHERE job_run_id = p_job_run_id;

    RETURN FOUND;
END;
$$;


--
-- Name: sp_scheduler_fail_stale_runs(interval); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_fail_stale_runs(p_stale_after interval DEFAULT '00:30:00'::interval) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_runs_updated integer := 0;
    v_requests_updated integer := 0;
BEGIN
    UPDATE public.scheduler_job_runs
    SET
        status = 'FAILED',
        completed_at = now(),
        error_message = COALESCE(
            error_message,
            'Run interrotto o processo non più disponibile'
        )
    WHERE status = 'RUNNING'
      AND completed_at IS NULL
      AND started_at < now() - p_stale_after;

    GET DIAGNOSTICS v_runs_updated = ROW_COUNT;

    UPDATE public.scheduler_manual_run_requests request
    SET
        status = 'FAILED',
        completed_at = now(),
        error_message = COALESCE(
            request.error_message,
            'Richiesta manuale interrotta o scheduler non più disponibile'
        )
    WHERE request.status = 'PROCESSING'
      AND request.completed_at IS NULL
      AND request.claimed_at < now() - p_stale_after
      AND NOT EXISTS (
          SELECT 1
          FROM public.scheduler_job_runs run
          WHERE run.job_run_id = request.job_run_id
            AND run.status = 'RUNNING'
            AND run.completed_at IS NULL
      );

    GET DIAGNOSTICS v_requests_updated = ROW_COUNT;

    RETURN v_runs_updated + v_requests_updated;
END;
$$;


--
-- Name: sp_scheduler_get_run_logs(uuid, bigint, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_get_run_logs(p_job_run_id uuid, p_after_log_id bigint DEFAULT 0, p_limit integer DEFAULT 500) RETURNS TABLE(log_id bigint, job_run_id uuid, logged_at timestamp with time zone, level character varying, logger_name character varying, message text)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        log.log_id,
        log.job_run_id,
        log.logged_at,
        log.level,
        log.logger_name,
        log.message
    FROM public.scheduler_job_run_logs log
    WHERE log.job_run_id = p_job_run_id
      AND log.log_id > greatest(p_after_log_id, 0)
    ORDER BY log.log_id
    LIMIT least(greatest(p_limit, 1), 2000);
$$;


--
-- Name: sp_scheduler_list_job_ids(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_list_job_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT job_id FROM public.scheduler_jobs;
$$;


--
-- Name: sp_scheduler_recover_job_runs(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_recover_job_runs(p_job_id uuid) RETURNS integer
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


--
-- Name: sp_scheduler_request_job_run(character varying, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_request_job_run(p_job_key character varying, p_requested_by uuid) RETURNS TABLE(request_id uuid, job_run_id uuid, status character varying)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    INSERT INTO public.scheduler_manual_run_requests(job_id, requested_by)
    SELECT sj.job_id, p_requested_by
    FROM public.scheduler_jobs sj
    WHERE sj.job_key = p_job_key
    RETURNING
        scheduler_manual_run_requests.request_id,
        scheduler_manual_run_requests.job_run_id,
        scheduler_manual_run_requests.status;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Scheduler job not found: %', p_job_key;
    END IF;

    PERFORM public.sp_record_system_audit(
        p_requested_by,
        'SCHEDULER_JOB_RUN_REQUESTED',
        'SCHEDULER_JOB',
        p_job_key,
        jsonb_build_object('source', 'ADMIN_UI')
    );
END;
$$;


--
-- Name: sp_scheduler_try_job_lock(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_try_job_lock(p_job_id uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT pg_try_advisory_xact_lock(hashtextextended('mip.scheduler.' || p_job_id::text, 0));
$$;


--
-- Name: sp_scheduler_update_next_run(uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_scheduler_update_next_run(p_job_id uuid, p_next_run_at timestamp with time zone) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.scheduler_jobs
    SET next_run_at = p_next_run_at
    WHERE job_id = p_job_id;

    RETURN FOUND;
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
    LANGUAGE sql STABLE
    AS $$
SELECT COALESCE(jsonb_agg(row_data), '[]'::jsonb)
FROM (
    SELECT jsonb_build_object(
        'symbol', i.symbol,
        'name', e.name,
        'exchange', i.exchange,
        'currency', i.currency_code,
        'type', i.instrument_type,
        'identifiers', COALESCE(ids.identifiers, '{}'::jsonb),
        'data_source', COALESCE(ps.code, i.data_source)
    ) AS row_data
    FROM public.instruments i
    JOIN public.entities e ON e.entity_id = i.entity_id
    LEFT JOIN public.external_systems ps ON ps.system_id = i.price_source_id
    LEFT JOIN LATERAL (
        SELECT jsonb_object_agg(it.code, id.identity_value) AS identifiers
        FROM public.instrument_identities ii
        JOIN public.identities id ON id.identity_id = ii.identity_id
        JOIN public.identity_types it ON it.identity_type_id = id.identity_type_id
        WHERE ii.instrument_id = i.instrument_id
    ) ids ON true
    WHERE lower(i.symbol) LIKE lower(p_query || '%')
       OR lower(e.name) LIKE lower(p_query || '%')
       OR EXISTS (
            SELECT 1
            FROM public.entity_name_observations eno
            WHERE eno.entity_id = e.entity_id
              AND eno.normalized_name LIKE public.sp_normalize_entity_name(p_query) || '%'
       )
       OR EXISTS (
            SELECT 1
            FROM public.entity_aliases ea
            WHERE ea.entity_id = e.entity_id
              AND public.sp_normalize_entity_name(ea.alias_name)
                  LIKE public.sp_normalize_entity_name(p_query) || '%'
       )
       OR EXISTS (
            SELECT 1
            FROM public.instrument_identities ii2
            JOIN public.identities id2 ON id2.identity_id = ii2.identity_id
            WHERE ii2.instrument_id = i.instrument_id
              AND id2.normalized_value LIKE upper(p_query || '%')
       )
    ORDER BY
        CASE
            WHEN lower(i.symbol) = lower(p_query) THEN 1
            WHEN lower(i.symbol) LIKE lower(p_query || '%') THEN 2
            WHEN lower(e.name) = lower(p_query) THEN 3
            ELSE 4
        END,
        i.symbol,
        i.exchange NULLS LAST
    LIMIT p_limit
) q;
$$;


--
-- Name: sp_set_canonical_entity_name(uuid, character varying, boolean, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_set_canonical_entity_name(p_entity_id uuid, p_name character varying, p_lock boolean DEFAULT true, p_system_code character varying DEFAULT 'MIP_LEGACY'::character varying) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_observation_id uuid;
BEGIN
    v_observation_id := public.sp_observe_entity_name(
        p_entity_id,
        p_system_code,
        p_name,
        'manual-canonical-name',
        1,
        jsonb_build_object('manual', true),
        true
    );

    UPDATE public.entities
    SET name_locked = p_lock
    WHERE entity_id = p_entity_id;

    RETURN v_observation_id;
END;
$$;


--
-- Name: sp_set_position_cost_basis(uuid, uuid, numeric, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_set_position_cost_basis(p_position_id uuid, p_user_id uuid, p_amount numeric, p_currency character) RETURNS uuid
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


--
-- Name: sp_set_scheduler_job_enabled(uuid, character varying, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_set_scheduler_job_enabled(p_actor uuid, p_key character varying, p_enabled boolean) RETURNS SETOF public.scheduler_jobs
    LANGUAGE plpgsql
    AS $$ BEGIN RETURN QUERY UPDATE scheduler_jobs SET enabled=p_enabled WHERE job_key=p_key RETURNING *; IF NOT FOUND THEN RAISE EXCEPTION 'Job not found: %',p_key; END IF; INSERT INTO system_audit_log(actor_user_id,action,resource_type,resource_id,new_value) VALUES(p_actor,CASE WHEN p_enabled THEN 'SCHEDULER_JOB_ENABLED' ELSE 'SCHEDULER_JOB_DISABLED' END,'SCHEDULER_JOB',p_key,jsonb_build_object('enabled',p_enabled)); END $$;


--
-- Name: sp_set_user_notification_source_enabled(uuid, uuid, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_set_user_notification_source_enabled(p_user_id uuid, p_source_id uuid, p_enabled boolean) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.user_sources us
    SET config = jsonb_set(
        COALESCE(us.config, '{}'::jsonb),
        '{enabled}',
        to_jsonb(p_enabled),
        true
    )
    WHERE us.user_id = p_user_id
      AND us.source_id = p_source_id;
    RETURN FOUND;
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
-- Name: sp_store_exchange_holidays(text, text, text, text, date, timestamp with time zone, timestamp with time zone, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_store_exchange_holidays(p_exchange text, p_exchange_code text, p_source text, p_provider_exchange text, p_as_of date, p_updated_at timestamp with time zone, p_fetched_at timestamp with time zone, p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_system_id uuid;
    v_count integer;
    v_snapshot_id uuid;
    v_payload jsonb;
BEGIN
    IF p_exchange_code IS NULL
        OR p_exchange_code !~ '^[A-Z0-9]{4,20}$'
        OR p_provider_exchange IS NULL
        OR btrim(p_provider_exchange) = ''
        OR p_source IS NULL
        OR p_as_of IS NULL
        OR p_updated_at IS NULL
        OR p_fetched_at IS NULL
        OR p_payload IS NULL
        OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
    THEN
        RAISE EXCEPTION 'Invalid holiday snapshot metadata';
    END IF;

    SELECT count(*) INTO v_count FROM public.external_systems
    WHERE lower(code) = lower(p_source) AND active;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Expected one active external system for %', p_source;
    END IF;
    SELECT system_id INTO v_system_id FROM public.external_systems
    WHERE lower(code) = lower(p_source) AND active;
    PERFORM 1 FROM public.exchanges WHERE exchange = p_exchange;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Existing exchange row required: %', p_exchange;
    END IF;
    LOCK TABLE public.exchange_source_mappings IN SHARE ROW EXCLUSIVE MODE;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE system_id = v_system_id AND exchange <> p_exchange
            AND lower(source_exchange_code) IN
                (lower(p_exchange_code), lower(p_provider_exchange))
    ) THEN
        RAISE EXCEPTION 'Provider exchange identity conflicts with existing mapping';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.exchange_holiday_snapshots
        WHERE (exchange_code = p_exchange_code AND exchange <> p_exchange)
            OR (exchange = p_exchange AND exchange_code <> p_exchange_code)
    ) THEN
        RAISE EXCEPTION 'Canonical exchange code already belongs to another row';
    END IF;
    SELECT snapshot_id, raw_payload INTO v_snapshot_id, v_payload
    FROM public.exchange_holiday_snapshots
    WHERE exchange_code = p_exchange_code AND source = lower(p_source)
        AND provider_updated_at = p_updated_at;
    IF FOUND THEN
        IF v_payload IS DISTINCT FROM p_payload THEN
            RAISE EXCEPTION 'Provider changed payload without changing its version';
        END IF;
        RETURN v_snapshot_id;
    END IF;
    INSERT INTO public.exchange_holiday_snapshots
        (exchange, exchange_code, source, provider_exchange, as_of,
         provider_updated_at, fetched_at, raw_payload)
    VALUES (p_exchange, p_exchange_code, lower(p_source), p_provider_exchange,
            p_as_of, p_updated_at, p_fetched_at, p_payload)
    RETURNING snapshot_id INTO v_snapshot_id;
    INSERT INTO public.exchange_source_mappings
        (exchange, system_id, source_exchange_code, source_exchange_name,
         observed_at, raw_payload)
    SELECT DISTINCT p_exchange, v_system_id, identifier, p_provider_exchange,
        p_fetched_at, jsonb_build_object('exchange_code', p_exchange_code,
            'provider_exchange', p_provider_exchange,
            'endpoint', '/market/holidays/' || p_exchange_code,
            'provider_updated_at', p_updated_at)
    FROM unnest(ARRAY[p_exchange_code, p_provider_exchange]) AS identifier
    ON CONFLICT (exchange, system_id, source_exchange_code) DO NOTHING;
    RETURN v_snapshot_id;
END;
$_$;


--
-- Name: sp_sync_exchange_catalog(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_sync_exchange_catalog(p_catalog jsonb) RETURNS integer
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_system_id uuid;
    v_item jsonb;
    v_code text;
    v_legacy text;
BEGIN
    IF jsonb_typeof(p_catalog) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Exchange catalog must be an array';
    END IF;
    IF jsonb_array_length(p_catalog) = 0 THEN
        RAISE EXCEPTION 'Exchange catalog must not be empty';
    END IF;
    SELECT system_id INTO STRICT v_system_id
    FROM public.external_systems
    WHERE lower(btrim(code)) = 'eulerpool' AND active;

    LOCK TABLE public.exchanges IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.exchange_source_mappings IN SHARE ROW EXCLUSIVE MODE;

    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_catalog) AS rows(item)
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR jsonb_typeof(item->'code') IS DISTINCT FROM 'string'
           OR COALESCE(item->>'code', '') !~ '^[A-Z0-9]{4,20}$'
           OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
           OR NULLIF(btrim(item->>'name'), '') IS NULL
           OR length(item->>'name') > 255
           OR length(item->>'country') > 10
           OR (NULLIF(item->>'timezone', '') IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_timezone_names WHERE name = item->>'timezone'
           ))
    ) THEN
        RAISE EXCEPTION 'Invalid Eulerpool exchange catalog entry';
    END IF;
    IF EXISTS (
        SELECT item->>'code' FROM jsonb_array_elements(p_catalog) AS rows(item)
        GROUP BY item->>'code' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate Eulerpool exchange catalog codes';
    END IF;

    SELECT string_agg(e.exchange, ', ' ORDER BY e.exchange) INTO v_legacy
    FROM public.exchanges e
    WHERE NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_catalog) AS rows(item)
        WHERE item->>'code' = e.exchange
    );
    IF v_legacy IS NOT NULL THEN
        RAISE EXCEPTION 'Noncanonical existing exchanges: %. Reconcile explicitly before catalog synchronization', v_legacy;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings m
        JOIN jsonb_array_elements(p_catalog) AS rows(item)
          ON upper(btrim(m.source_exchange_code)) = item->>'code'
          OR upper(btrim(m.source_exchange_name)) = upper(btrim(item->>'name'))
        WHERE m.system_id = v_system_id AND m.exchange <> item->>'code'
    ) THEN
        RAISE EXCEPTION 'Eulerpool catalog conflicts with existing exchange mappings';
    END IF;

    FOR v_item IN SELECT item FROM jsonb_array_elements(p_catalog) AS rows(item)
    LOOP
        v_code := v_item->>'code';
        INSERT INTO public.exchanges(exchange, exchangefullname, country_code, timezone_name)
        VALUES (v_code, btrim(v_item->>'name'), NULLIF(v_item->>'country', ''), NULLIF(v_item->>'timezone', ''))
        ON CONFLICT (exchange) DO UPDATE SET
            exchangefullname = EXCLUDED.exchangefullname,
            country_code = EXCLUDED.country_code,
            timezone_name = COALESCE(EXCLUDED.timezone_name, exchanges.timezone_name),
            updated_at = now();

        INSERT INTO public.exchange_source_mappings
            (exchange, system_id, source_exchange_code, source_exchange_name, raw_payload)
        VALUES (v_code, v_system_id, v_code, btrim(v_item->>'name'),
            jsonb_build_object('authority', 'eulerpool_catalog', 'catalog', v_item->'raw_payload'))
        ON CONFLICT (exchange, system_id, source_exchange_code) DO UPDATE SET
            source_exchange_name = EXCLUDED.source_exchange_name,
            raw_payload = EXCLUDED.raw_payload,
            observed_at = now();
    END LOOP;
    RETURN jsonb_array_length(p_catalog);
END;
$_$;


--
-- Name: sp_update_entity_profile(uuid, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_update_entity_profile(p_entity_id uuid, p_name character varying, p_country_code character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NULLIF(btrim(p_name), '') IS NOT NULL THEN
        PERFORM public.sp_set_canonical_entity_name(
            p_entity_id,
            p_name,
            true,
            'MIP_LEGACY'
        );
    END IF;

    UPDATE public.entities e
    SET country_code = COALESCE(
        NULLIF(upper(btrim(p_country_code)), ''),
        e.country_code
    )
    WHERE e.entity_id = p_entity_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entity not found: %', p_entity_id;
    END IF;
END;
$$;


--
-- Name: sp_update_scheduler_job(uuid, character varying, character varying, character varying, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_update_scheduler_job(p_actor uuid, p_key character varying, p_type character varying, p_cron character varying, p_minutes integer) RETURNS SETOF public.scheduler_jobs
    LANGUAGE plpgsql
    AS $$ DECLARE v varchar:=lower(trim(p_type)); BEGIN IF v NOT IN('cron','interval') OR (v='cron' AND nullif(trim(p_cron),'') IS NULL) OR (v='interval' AND coalesce(p_minutes,0)<=0) THEN RAISE EXCEPTION 'Invalid schedule'; END IF; RETURN QUERY UPDATE scheduler_jobs SET trigger_type=v,cron_expression=CASE WHEN v='cron' THEN p_cron END,interval_minutes=CASE WHEN v='interval' THEN p_minutes END WHERE job_key=p_key RETURNING *; INSERT INTO system_audit_log(actor_user_id,action,resource_type,resource_id,new_value) VALUES(p_actor,'SCHEDULER_JOB_SCHEDULE_CHANGED','SCHEDULER_JOB',p_key,jsonb_build_object('trigger_type',v,'cron_expression',p_cron,'interval_minutes',p_minutes)); END $$;


--
-- Name: sp_update_user_preferences(uuid, character); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_update_user_preferences(p_user_id uuid, p_reporting_currency character) RETURNS jsonb
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


--
-- Name: sp_upsert_asset_profile(jsonb, uuid, uuid, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_asset_profile(p_payload jsonb, p_entity_id uuid DEFAULT NULL::uuid, p_instrument_id uuid DEFAULT NULL::uuid, p_create_if_missing boolean DEFAULT true) RETURNS TABLE(entity_id uuid, instrument_id uuid, imported boolean, action character varying)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_symbol varchar(50);
    v_name varchar(255);
    v_exchange varchar(50);
    v_exchange_full_name varchar(255);
    v_currency char(3);
    v_entity_type public.entity_type;
    v_instrument_type varchar(50);
    v_country_code varchar(2);
    v_data_source varchar(50);
    v_observation_source varchar(50);
    v_last_price numeric(20,8);
    v_entity_id uuid;
    v_instrument_id uuid;
    v_existing_symbol varchar(50);
    v_existing_exchange varchar(50);
    v_existing_currency char(3);
    v_existing_name varchar(255);
    v_name_locked boolean;
    v_canonical_observation_id uuid;
    v_name_observation_id uuid;
    v_stub_count integer;
    v_name_candidate_count integer;
    v_is_new_entity boolean := false;
    v_is_new_instrument boolean := false;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'Profile payload must be a JSON object';
    END IF;

    v_symbol := upper(nullif(btrim(p_payload->>'symbol'), ''));
    IF v_symbol IS NULL AND p_instrument_id IS NULL THEN
        RAISE EXCEPTION 'Payload symbol is required';
    END IF;

    v_name := nullif(btrim(coalesce(
        p_payload->>'name',
        p_payload->>'companyName'
    )), '');

    v_data_source := upper(nullif(btrim(coalesce(
        p_payload->>'source',
        p_payload->>'data_source',
        p_payload->>'metadata_source'
    )), ''));

    v_exchange := public.sp_normalize_listing_exchange(
        v_symbol,
        coalesce(p_payload->>'exchange_code', p_payload->>'exchange'),
        v_data_source
    );
    v_exchange_full_name := nullif(btrim(coalesce(
        p_payload->>'exchangeFullName',
        p_payload->>'exchange_full_name'
    )), '');
    v_currency := upper(nullif(btrim(p_payload->>'currency'), ''));
    v_instrument_type := coalesce(
        upper(nullif(btrim(coalesce(
            p_payload->>'instrument_type',
            p_payload->>'asset_sub_class',
            p_payload->>'type'
        )), '')),
        'STOCK'
    );
    v_entity_type := CASE
        WHEN upper(coalesce(p_payload->>'asset_class', '')) IN ('CRYPTO', 'CRYPTOCURRENCY')
            THEN 'CRYPTO'::public.entity_type
        WHEN v_instrument_type = 'ETF' THEN 'ETF'::public.entity_type
        WHEN v_instrument_type IN ('FUND', 'MUTUALFUND', 'MUTUAL_FUND')
            THEN 'FUND'::public.entity_type
        WHEN v_instrument_type = 'INDEX' THEN 'INDEX'::public.entity_type
        WHEN v_instrument_type = 'COMMODITY' THEN 'COMMODITY'::public.entity_type
        ELSE 'COMPANY'::public.entity_type
    END;
    v_country_code := upper(nullif(btrim(coalesce(
        p_payload->>'country_code',
        p_payload->>'country'
    )), ''));
    v_last_price := nullif(btrim(p_payload->>'last_price'), '')::numeric;

    IF v_exchange IS NOT NULL THEN
        PERFORM public.sp_upsert_exchange(v_exchange, v_exchange_full_name);
    END IF;

    -- Explicit IDs are the refresh/update mode. The stored listing identity is
    -- authoritative; provider results may fill missing exchange/currency but
    -- may not silently switch to a different listing.
    IF p_instrument_id IS NOT NULL THEN
        SELECT
            i.entity_id,
            i.symbol,
            i.exchange,
            i.currency_code,
            e.name,
            coalesce(e.name_locked, false),
            e.canonical_name_observation_id
        INTO
            v_entity_id,
            v_existing_symbol,
            v_existing_exchange,
            v_existing_currency,
            v_existing_name,
            v_name_locked,
            v_canonical_observation_id
        FROM public.instruments i
        JOIN public.entities e ON e.entity_id = i.entity_id
        WHERE i.instrument_id = p_instrument_id
        FOR UPDATE OF i, e;

        IF v_entity_id IS NULL THEN
            RAISE EXCEPTION 'Instrument not found: %', p_instrument_id;
        END IF;
        IF p_entity_id IS NOT NULL AND p_entity_id <> v_entity_id THEN
            RAISE EXCEPTION 'Instrument % does not belong to entity %',
                p_instrument_id, p_entity_id;
        END IF;

        v_instrument_id := p_instrument_id;
        v_symbol := v_existing_symbol;
        v_exchange := coalesce(v_existing_exchange, v_exchange);
        v_currency := coalesce(v_existing_currency, v_currency);
    ELSE
        -- Serialize all writers for the normalized listing key.
        PERFORM pg_advisory_xact_lock(hashtextextended(
            coalesce(v_symbol, '') || '|' ||
            coalesce(v_exchange, '') || '|' ||
            coalesce(v_currency::text, ''),
            0
        ));

        SELECT i.entity_id, i.instrument_id
        INTO v_entity_id, v_instrument_id
        FROM public.instruments i
        WHERE upper(btrim(i.symbol)) = v_symbol
          AND coalesce(upper(btrim(i.exchange)), '') = coalesce(v_exchange, '')
          AND coalesce(upper(btrim(i.currency_code)), '') = coalesce(v_currency::text, '')
        ORDER BY i.created_at, i.instrument_id
        LIMIT 1
        FOR UPDATE;

        -- Upgrade one unambiguous stub instead of creating a second listing.
        IF v_instrument_id IS NULL AND v_exchange IS NOT NULL THEN
            SELECT
                count(*),
                (array_agg(i.instrument_id ORDER BY i.instrument_id))[1],
                (array_agg(i.entity_id ORDER BY i.instrument_id))[1]
            INTO v_stub_count, v_instrument_id, v_entity_id
            FROM public.instruments i
            WHERE upper(btrim(i.symbol)) = v_symbol
              AND i.exchange IS NULL
              AND (
                  i.currency_code IS NULL
                  OR v_currency IS NULL
                  OR upper(btrim(i.currency_code)) = v_currency
              );

            IF v_stub_count <> 1 THEN
                v_instrument_id := NULL;
                v_entity_id := NULL;
            END IF;
        END IF;

        IF v_instrument_id IS NULL THEN
            v_entity_id := public.sp_resolve_entity_from_profile_identities(p_payload);

            IF v_entity_id IS NULL AND v_name IS NOT NULL THEN
                SELECT
                    count(*),
                    (array_agg(e.entity_id ORDER BY e.entity_id))[1]
                INTO v_name_candidate_count, v_entity_id
                FROM public.entities e
                WHERE public.sp_normalize_entity_name(e.name) =
                      public.sp_normalize_entity_name(v_name)
                  AND e.entity_type = v_entity_type
                  AND (
                      e.country_code IS NULL
                      OR v_country_code IS NULL
                      OR upper(btrim(e.country_code)) = v_country_code
                  );

                IF v_name_candidate_count <> 1 THEN
                    v_entity_id := NULL;
                END IF;
            END IF;

            IF v_entity_id IS NULL THEN
                IF NOT p_create_if_missing THEN
                    RAISE EXCEPTION 'No matching entity/listing found for %', v_symbol;
                END IF;

                INSERT INTO public.entities(name, entity_type, country_code)
                VALUES (coalesce(v_name, v_symbol), v_entity_type, v_country_code)
                RETURNING entities.entity_id INTO v_entity_id;
                v_is_new_entity := true;
            END IF;

            INSERT INTO public.instruments(
                entity_id,
                symbol,
                exchange,
                instrument_type,
                currency_code,
                data_source,
                last_price
            )
            VALUES (
                v_entity_id,
                v_symbol,
                v_exchange,
                v_instrument_type,
                v_currency,
                v_data_source,
                v_last_price
            )
            RETURNING instruments.instrument_id INTO v_instrument_id;
            v_is_new_instrument := true;
        END IF;
    END IF;

    SELECT
        e.name,
        coalesce(e.name_locked, false),
        e.canonical_name_observation_id
    INTO v_existing_name, v_name_locked, v_canonical_observation_id
    FROM public.entities e
    WHERE e.entity_id = v_entity_id
    FOR UPDATE;

    SELECT es.code
    INTO v_observation_source
    FROM public.external_systems es
    WHERE es.active = true
      AND upper(es.code) = v_data_source
    LIMIT 1;

    IF v_observation_source IS NULL THEN
        SELECT es.code
        INTO v_observation_source
        FROM public.external_systems es
        WHERE es.active = true
          AND upper(es.code) = 'MIP_LEGACY'
        LIMIT 1;
    END IF;

    IF v_name IS NOT NULL AND v_observation_source IS NOT NULL THEN
        v_name_observation_id := public.sp_observe_entity_name(
            v_entity_id,
            v_observation_source,
            v_name,
            coalesce(p_payload->>'source_reference', p_payload->>'provider_id'),
            NULL,
            p_payload
        );
    END IF;

    UPDATE public.entities e
    SET
        name = CASE
            WHEN v_name IS NOT NULL
             AND NOT v_name_locked
             AND (
                 v_canonical_observation_id IS NULL
                 OR upper(btrim(v_existing_name)) = upper(btrim(v_symbol))
             )
                THEN v_name
            ELSE e.name
        END,
        canonical_name_observation_id = CASE
            WHEN v_name_observation_id IS NOT NULL
             AND NOT v_name_locked
             AND (
                 v_canonical_observation_id IS NULL
                 OR upper(btrim(v_existing_name)) = upper(btrim(v_symbol))
             )
                THEN v_name_observation_id
            ELSE e.canonical_name_observation_id
        END,
        entity_type = coalesce(e.entity_type, v_entity_type),
        country_code = coalesce(e.country_code, v_country_code)
    WHERE e.entity_id = v_entity_id;

    UPDATE public.instruments i
    SET
        exchange = coalesce(i.exchange, v_exchange),
        instrument_type = coalesce(nullif(v_instrument_type, 'UNKNOWN'), i.instrument_type),
        currency_code = coalesce(i.currency_code, v_currency),
        data_source = coalesce(v_data_source, i.data_source),
        last_price = coalesce(v_last_price, i.last_price)
    WHERE i.instrument_id = v_instrument_id;

    -- Identities, observations, price source and price timestamp are persisted
    -- in the same database transaction as entity/listing resolution.
    PERFORM public.sp_apply_instrument_reference_metadata(v_instrument_id, p_payload);

    RETURN QUERY
    SELECT
        v_entity_id,
        v_instrument_id,
        v_is_new_instrument,
        CASE
            WHEN v_is_new_entity THEN 'CREATED_ENTITY_AND_INSTRUMENT'
            WHEN v_is_new_instrument THEN 'CREATED_INSTRUMENT'
            ELSE 'UPDATED'
        END::varchar;
END;
$$;


--
-- Name: sp_upsert_catalyst(uuid, text, text, timestamp with time zone, text, text, text, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_catalyst(p_entity_id uuid, p_symbol text, p_entity_subtype text, p_entity_date timestamp with time zone, p_entity_status text, p_importance text, p_title text, p_source text, p_payload jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_entity_id uuid;
    v_catalyst_id uuid;
    v_catalyst_type varchar(50);
    v_importance numeric;
	v_catalyst_day date;
    v_symbol text;
    v_matching_entity_ids uuid[];
BEGIN
    v_symbol := upper(nullif(btrim(p_symbol), ''));

    v_catalyst_type :=
        CASE upper(coalesce(p_entity_subtype, ''))
            WHEN 'QUARTERLY_EARNINGS' THEN 'EARNINGS'
            WHEN 'EARNINGS' THEN 'EARNINGS'
            WHEN 'CASH_DIVIDEND' THEN 'DIVIDEND'
            WHEN 'DIVIDEND' THEN 'DIVIDEND'
            WHEN 'STOCK_SPLIT' THEN 'SPLIT'
            WHEN 'SPLIT' THEN 'SPLIT'
            ELSE 'OTHER'
        END;

    v_importance :=
        CASE upper(coalesce(p_importance, 'MEDIUM'))
            WHEN 'HIGH' THEN 90
            WHEN 'MEDIUM' THEN 50
            WHEN 'LOW' THEN 10
            ELSE 50
        END;
	v_catalyst_day :=
	    COALESCE(
	        NULLIF(
	            p_payload->>'date',
	            ''
	        )::date,
	        (p_entity_date AT TIME ZONE 'UTC')::date
	    );
    /*
     * L'entity_id esplicito è sempre autorevole.
     */
    IF p_entity_id IS NOT NULL THEN
        SELECT e.entity_id
        INTO v_entity_id
        FROM public.entities e
        WHERE e.entity_id = p_entity_id
          AND e.active = true;

        IF v_entity_id IS NULL THEN
            RAISE EXCEPTION
                'Active entity not found: %',
                p_entity_id;
        END IF;

        IF v_symbol IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM public.instruments i
               WHERE i.entity_id = v_entity_id
                 AND upper(btrim(i.symbol)) = v_symbol
           )
        THEN
            RAISE EXCEPTION
                'Symbol % does not belong to entity %',
                p_symbol,
                v_entity_id;
        END IF;
    ELSE
        IF v_symbol IS NULL THEN
            RAISE EXCEPTION
                'Either p_entity_id or p_symbol is required';
        END IF;

        SELECT array_agg(
            DISTINCT i.entity_id
            ORDER BY i.entity_id
        )
        INTO v_matching_entity_ids
        FROM public.instruments i
        JOIN public.entities e
          ON e.entity_id = i.entity_id
        WHERE e.active = true
          AND upper(btrim(i.symbol)) = v_symbol;

        IF COALESCE(array_length(v_matching_entity_ids, 1), 0) = 0 THEN
            RAISE EXCEPTION
                'No entity found for symbol %',
                p_symbol;
        END IF;

        IF array_length(v_matching_entity_ids, 1) > 1 THEN
            RAISE EXCEPTION
                'Symbol % resolves to multiple entities: %',
                p_symbol,
                v_matching_entity_ids;
        END IF;

        v_entity_id := v_matching_entity_ids[1];
    END IF;

	INSERT INTO public.catalysts (
	    entity_id,
	    catalyst_type,
	    catalyst_day,
	    catalyst_date,
	    status,
	    importance,
	    title,
	    source,
	    raw_payload
	)
    VALUES (
        v_entity_id,
        v_catalyst_type,
		v_catalyst_day,
        p_entity_date,
        p_entity_status,
        v_importance,
        p_title,
        p_source,
        COALESCE(p_payload, '{}'::jsonb)
        || jsonb_strip_nulls(
            jsonb_build_object(
                'source_symbol', v_symbol,
                'entity_id', v_entity_id
            )
        )
    )
	ON CONFLICT (
		entity_id,
		catalyst_type,
		source,
		catalyst_day
	)
    DO UPDATE SET
	    catalyst_date = COALESCE(
	        EXCLUDED.catalyst_date,
	        public.catalysts.catalyst_date
	    ),
        status = EXCLUDED.status,
        importance = GREATEST(
            COALESCE(public.catalysts.importance, 0),
            COALESCE(EXCLUDED.importance, 0)
        ),
        title = COALESCE(
            NULLIF(EXCLUDED.title, ''),
            public.catalysts.title
        ),
        source = COALESCE(
            NULLIF(EXCLUDED.source, ''),
            public.catalysts.source
        ),
        raw_payload =
            COALESCE(public.catalysts.raw_payload, '{}'::jsonb)
            || COALESCE(EXCLUDED.raw_payload, '{}'::jsonb)
    RETURNING catalyst_id
    INTO v_catalyst_id;

    RETURN v_catalyst_id;
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

    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE raw_payload->>'authority' = 'eulerpool_catalog'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.exchange_source_mappings
            WHERE exchange = p_exchange
              AND source_exchange_code = p_exchange
              AND raw_payload->>'authority' = 'eulerpool_catalog'
        ) THEN
            RAISE EXCEPTION 'Unknown canonical exchange %; register a provider alias or refresh the Eulerpool catalog', p_exchange;
        END IF;
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
-- Name: sp_upsert_exchange_rate_history(character, date, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_exchange_rate_history(p_currency_code character, p_rate_date date, p_rate_to_usd numeric) RETURNS void
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_currency_code char(3);
BEGIN
    v_currency_code := upper(btrim(p_currency_code));

    IF v_currency_code IS NULL
       OR length(v_currency_code) <> 3
       OR v_currency_code !~ '^[A-Z]{3}$'
    THEN
        RAISE EXCEPTION 'Invalid currency code: %', p_currency_code;
    END IF;

    IF p_rate_date IS NULL THEN
        RAISE EXCEPTION 'p_rate_date is required';
    END IF;

    IF p_rate_to_usd IS NULL OR p_rate_to_usd <= 0 THEN
        RAISE EXCEPTION 'Invalid exchange rate for % on %: %',
            v_currency_code, p_rate_date, p_rate_to_usd;
    END IF;

    INSERT INTO public.exchange_rate_history (
        currency_code,
        rate_date,
        rate_to_usd
    )
    VALUES (
        v_currency_code,
        p_rate_date,
        p_rate_to_usd
    )
    ON CONFLICT (currency_code, rate_date)
    DO UPDATE SET rate_to_usd = EXCLUDED.rate_to_usd;
END;
$_$;


--
-- Name: sp_upsert_filing(text, text, text, date, timestamp with time zone, text, text, jsonb, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_filing(p_symbol text, p_filing_type text, p_cik text, p_filing_date date, p_accepted_at timestamp with time zone, p_title text, p_source text, p_raw_payload jsonb, p_filing_subtype text DEFAULT NULL::text, p_accession_number text DEFAULT NULL::text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_entity_id uuid;
    v_cik_identity_type_id uuid;
    v_cik_identity_id uuid;
    v_normalized_cik text;
    v_symbol text;
BEGIN
    v_symbol := nullif(upper(btrim(p_symbol)), '');
    v_normalized_cik := nullif(regexp_replace(coalesce(p_cik, ''), '[^0-9]', '', 'g'), '');

    ------------------------------------------------------------------
    -- Resolve the entity by CIK first. A SEC filing identifies the
    -- issuer, but does not provide enough listing metadata to create a
    -- complete instrument (exchange and currency are unavailable).
    ------------------------------------------------------------------
    IF v_normalized_cik IS NOT NULL THEN
        SELECT it.identity_type_id
          INTO v_cik_identity_type_id
          FROM public.identity_types it
         WHERE upper(it.code) = 'CIK'
           AND upper(it.target_kind) = 'ENTITY'
         ORDER BY it.identity_type_id
         LIMIT 1;

        IF v_cik_identity_type_id IS NOT NULL THEN
            SELECT ei.entity_id,
                   identity_row.identity_id
              INTO v_entity_id,
                   v_cik_identity_id
              FROM public.identities identity_row
              JOIN public.entity_identities ei
                ON ei.identity_id = identity_row.identity_id
             WHERE identity_row.identity_type_id = v_cik_identity_type_id
               AND identity_row.normalized_value = v_normalized_cik
             ORDER BY ei.is_primary DESC,
                      ei.verified_at DESC NULLS LAST,
                      ei.created_at DESC
             LIMIT 1;
        END IF;
    END IF;

    ------------------------------------------------------------------
    -- Fall back to an existing instrument symbol only when the CIK did
    -- not already identify the entity.
    ------------------------------------------------------------------
    IF v_entity_id IS NULL AND v_symbol IS NOT NULL THEN
        SELECT instrument.entity_id
          INTO v_entity_id
          FROM public.instruments instrument
         WHERE upper(btrim(instrument.symbol)) = v_symbol
         ORDER BY
             (instrument.exchange IS NULL),
             instrument.updated_at DESC NULLS LAST,
             instrument.created_at DESC NULLS LAST,
             instrument.instrument_id
         LIMIT 1;
    END IF;

    ------------------------------------------------------------------
    -- Create only the issuer entity when it is still unknown. Do not
    -- create an incomplete instrument from a filing feed.
    ------------------------------------------------------------------
    IF v_entity_id IS NULL THEN
        INSERT INTO public.entities (
            name,
            entity_type
        )
        VALUES (
            coalesce(v_symbol, v_normalized_cik, 'UNKNOWN SEC ISSUER'),
            'COMPANY'
        )
        RETURNING entity_id
             INTO v_entity_id;
    END IF;

    ------------------------------------------------------------------
    -- Persist the CIK identity when the identity type exists and this
    -- CIK is not yet linked. The explicit existence checks avoid
    -- depending on undocumented conflict targets.
    ------------------------------------------------------------------
    IF v_cik_identity_type_id IS NOT NULL
       AND v_normalized_cik IS NOT NULL
       AND v_cik_identity_id IS NULL THEN
        SELECT identity_row.identity_id
          INTO v_cik_identity_id
          FROM public.identities identity_row
         WHERE identity_row.identity_type_id = v_cik_identity_type_id
           AND identity_row.normalized_value = v_normalized_cik
         LIMIT 1;

        IF v_cik_identity_id IS NULL THEN
            INSERT INTO public.identities (
                identity_type_id,
                identity_value,
                normalized_value
            )
            VALUES (
                v_cik_identity_type_id,
                coalesce(nullif(btrim(p_cik), ''), v_normalized_cik),
                v_normalized_cik
            )
            RETURNING identity_id
                 INTO v_cik_identity_id;
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM public.entity_identities ei
             WHERE ei.entity_id = v_entity_id
               AND ei.identity_id = v_cik_identity_id
        ) THEN
            INSERT INTO public.entity_identities (
                entity_id,
                identity_id,
                is_primary,
                verified_at
            )
            VALUES (
                v_entity_id,
                v_cik_identity_id,
                NOT EXISTS (
                    SELECT 1
                      FROM public.entity_identities existing_identity
                     WHERE existing_identity.entity_id = v_entity_id
                ),
                now()
            );
        END IF;
    END IF;

    ------------------------------------------------------------------
    -- Upsert the filing at entity level.
    ------------------------------------------------------------------
    INSERT INTO public.filings (
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
    VALUES (
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
    ON CONFLICT (
        cik,
        accepted_at,
        filing_type
    )
    DO UPDATE
       SET entity_id        = EXCLUDED.entity_id,
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
-- Name: sp_upsert_identity(character varying, uuid, character varying, character varying, character varying, character varying, numeric, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_identity(p_target_kind character varying, p_target_id uuid, p_identity_code character varying, p_identity_value character varying, p_system_code character varying DEFAULT NULL::character varying, p_source_reference character varying DEFAULT NULL::character varying, p_confidence numeric DEFAULT NULL::numeric, p_raw_payload jsonb DEFAULT NULL::jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_requested_target_kind varchar;
    v_identity_code varchar;
    v_value varchar;
    v_normalized varchar;
    v_identity_type_id uuid;
    v_registered_target_kind varchar;
    v_case_sensitive boolean;
    v_value_pattern text;
    v_identity_id uuid;
    v_system_id uuid;
BEGIN
    v_requested_target_kind := upper(btrim(p_target_kind));
    v_identity_code := upper(btrim(p_identity_code));
    v_value := NULLIF(btrim(p_identity_value), '');

    IF v_requested_target_kind NOT IN ('ENTITY', 'INSTRUMENT') THEN
        RAISE EXCEPTION 'Invalid identity target kind: %', p_target_kind;
    END IF;

    IF v_value IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT
        it.identity_type_id,
        it.target_kind,
        it.case_sensitive,
        it.value_pattern
    INTO
        v_identity_type_id,
        v_registered_target_kind,
        v_case_sensitive,
        v_value_pattern
    FROM public.identity_types it
    WHERE it.target_kind = v_requested_target_kind
      AND it.code = v_identity_code;

    IF v_identity_type_id IS NULL THEN
        RAISE EXCEPTION 'Identity type % is not valid for %',
            p_identity_code, p_target_kind;
    END IF;

    IF v_value_pattern IS NOT NULL AND v_value !~ v_value_pattern THEN
        RAISE EXCEPTION 'Invalid value for identity type %: %',
            v_identity_code, v_value;
    END IF;

    v_normalized := CASE
        WHEN v_case_sensitive THEN v_value
        ELSE upper(v_value)
    END;

    INSERT INTO public.identities (
        identity_type_id,
        identity_value,
        normalized_value
    )
    VALUES (
        v_identity_type_id,
        v_value,
        v_normalized
    )
    ON CONFLICT (identity_type_id, normalized_value)
    DO UPDATE SET
        identity_value = CASE
            WHEN identities.identity_value IS NULL THEN EXCLUDED.identity_value
            ELSE identities.identity_value
        END,
        updated_at = now()
    RETURNING identity_id INTO v_identity_id;

    IF v_registered_target_kind = 'ENTITY' THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.entities WHERE entity_id = p_target_id
        ) THEN
            RAISE EXCEPTION 'Entity not found: %', p_target_id;
        END IF;

        INSERT INTO public.entity_identities (entity_id, identity_id)
        VALUES (p_target_id, v_identity_id)
        ON CONFLICT DO NOTHING;
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM public.instruments WHERE instrument_id = p_target_id
        ) THEN
            RAISE EXCEPTION 'Instrument not found: %', p_target_id;
        END IF;

        INSERT INTO public.instrument_identities (instrument_id, identity_id)
        VALUES (p_target_id, v_identity_id)
        ON CONFLICT DO NOTHING;
    END IF;

    IF NULLIF(btrim(p_system_code), '') IS NOT NULL THEN
        SELECT es.system_id
        INTO v_system_id
        FROM public.external_systems es
        WHERE es.code = upper(btrim(p_system_code))
          AND es.active = true;

        IF v_system_id IS NULL THEN
            RAISE EXCEPTION 'Active external system not found: %', p_system_code;
        END IF;

        INSERT INTO public.identity_observations (
            identity_id,
            system_id,
            source_reference,
            confidence,
            raw_payload
        )
        VALUES (
            v_identity_id,
            v_system_id,
            NULLIF(btrim(p_source_reference), ''),
            p_confidence,
            p_raw_payload
        )
        ON CONFLICT (
            identity_id,
            system_id,
            (COALESCE(source_reference, ''::character varying))
        )
        DO UPDATE SET
            confidence = COALESCE(
                EXCLUDED.confidence,
                identity_observations.confidence
            ),
            observed_at = now(),
            raw_payload = COALESCE(
                EXCLUDED.raw_payload,
                identity_observations.raw_payload
            );
    END IF;

    RETURN v_identity_id;
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


--
-- Name: sp_upsert_user_notification_preference(uuid, character varying, boolean, uuid, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_user_notification_preference(p_user_id uuid, p_notification_type character varying, p_enabled boolean, p_source_id uuid, p_severity_min character varying DEFAULT NULL::character varying) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_notification_type varchar;
    v_severity_min varchar;
    v_subscription_id uuid;
BEGIN
    v_notification_type := UPPER(NULLIF(BTRIM(p_notification_type), ''));
    v_severity_min := UPPER(NULLIF(BTRIM(p_severity_min), ''));

    IF v_notification_type NOT IN (
        'UPCOMING_CATALYST',
        'BREAKING_CATALYST',
        'DAILY_MOVER'
    ) THEN
        RAISE EXCEPTION 'Unsupported notification type: %', p_notification_type;
    END IF;

    IF v_severity_min IS NOT NULL
       AND v_severity_min NOT IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    THEN
        RAISE EXCEPTION 'Unsupported minimum severity: %', p_severity_min;
    END IF;

    IF p_enabled AND p_source_id IS NULL THEN
        RAISE EXCEPTION 'An enabled notification preference requires a source';
    END IF;

    IF p_source_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM public.user_sources us
        WHERE us.source_id = p_source_id
          AND us.user_id = p_user_id
          AND COALESCE((us.config->>'enabled')::boolean, true) = true
    ) THEN
        RAISE EXCEPTION 'Enabled notification source does not belong to user';
    END IF;

    INSERT INTO public.notification_subscriptions (
        user_id,
        portfolio_id,
        event_type,
        source_id,
        enabled,
        severity_min,
        config
    )
    VALUES (
        p_user_id,
        NULL,
        v_notification_type,
        p_source_id,
        p_enabled,
        v_severity_min,
        '{}'::jsonb
    )
    ON CONFLICT (user_id, event_type)
    WHERE portfolio_id IS NULL
    DO UPDATE SET
        source_id = EXCLUDED.source_id,
        enabled = EXCLUDED.enabled,
        severity_min = EXCLUDED.severity_min
    RETURNING subscription_id INTO v_subscription_id;

    RETURN v_subscription_id;
END;
$$;


--
-- Name: sp_upsert_user_notification_source(uuid, uuid, character varying, character varying, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sp_upsert_user_notification_source(p_user_id uuid, p_source_id uuid, p_source_type character varying, p_source_name character varying, p_external_id text, p_config jsonb) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source_id uuid;
    v_source_type varchar;
    v_source_name varchar;
    v_external_id text;
BEGIN
    v_source_type := UPPER(NULLIF(BTRIM(p_source_type), ''));
    v_source_name := NULLIF(BTRIM(p_source_name), '');
    v_external_id := NULLIF(BTRIM(p_external_id), '');

    IF v_source_type NOT IN ('TELEGRAM') THEN
        RAISE EXCEPTION 'Unsupported notification source type: %', p_source_type;
    END IF;
    IF v_source_name IS NULL THEN
        RAISE EXCEPTION 'Notification source name cannot be empty';
    END IF;
    IF v_external_id IS NULL THEN
        RAISE EXCEPTION 'Notification destination cannot be empty';
    END IF;

    IF p_source_id IS NULL THEN
        INSERT INTO public.user_sources (
            user_id, source_type, source_name, external_id, config
        )
        VALUES (
            p_user_id,
            v_source_type,
            v_source_name,
            v_external_id,
            COALESCE(p_config, '{}'::jsonb)
        )
        RETURNING source_id INTO v_source_id;
    ELSE
        UPDATE public.user_sources us
        SET source_type = v_source_type,
            source_name = v_source_name,
            external_id = v_external_id,
            config = COALESCE(p_config, '{}'::jsonb)
        WHERE us.source_id = p_source_id
          AND us.user_id = p_user_id
        RETURNING us.source_id INTO v_source_id;

        IF v_source_id IS NULL THEN
            RAISE EXCEPTION 'Notification source not found';
        END IF;
    END IF;

    RETURN v_source_id;
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


--
-- Name: trg_llm_response_cache_timestamps(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_llm_response_cache_timestamps() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := COALESCE(NEW.created_at, now());
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: trg_market_screener_populate_signal_fields(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_market_screener_populate_signal_fields() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_signal record;
BEGIN
    SELECT
        signal.strategy_code,
        signal.signal_type,
        signal.timeframe,
        signal.trend,
        signal.confidence,
        signal.reference_price,
        signal.invalidation_price,
        signal.target_price,
        CASE
            WHEN jsonb_typeof(signal.evidence->'reasons') = 'array'
                THEN signal.evidence->'reasons'
            WHEN jsonb_typeof(signal.evidence->'signal'->'reasons') = 'array'
                THEN signal.evidence->'signal'->'reasons'
            ELSE '[]'::jsonb
        END AS reasons,
        COALESCE(
            signal.evidence->'technical',
            signal.evidence->'signal'->'evidence'->'technical',
            '{}'::jsonb
        ) AS technical_evidence
    INTO v_signal
    FROM public.trading_signals signal
    WHERE signal.signal_id = NEW.signal_id;

    IF FOUND THEN
        NEW.strategy_code := v_signal.strategy_code;
        NEW.signal_type := v_signal.signal_type;
        NEW.signal_timeframe := v_signal.timeframe;
        NEW.signal_trend := v_signal.trend;
        NEW.signal_confidence := v_signal.confidence;
        NEW.reference_price := v_signal.reference_price;
        NEW.invalidation_price := v_signal.invalidation_price;
        NEW.target_price := v_signal.target_price;
        NEW.signal_reasons := COALESCE(v_signal.reasons, '[]'::jsonb);
        NEW.technical_evidence := COALESCE(
            v_signal.technical_evidence,
            '{}'::jsonb
        );
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: trg_market_screener_set_entity_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_market_screener_set_entity_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    SELECT instrument.entity_id
    INTO NEW.entity_id
    FROM public.instruments instrument
    WHERE instrument.instrument_id = NEW.instrument_id;

    IF NEW.entity_id IS NULL THEN
        RAISE EXCEPTION 'Instrument not found for screener opportunity: %',
            NEW.instrument_id;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: trg_market_screener_snapshot_consensus(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_market_screener_snapshot_consensus() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN SELECT o.strategy_count,o.bullish_strategy_count,o.bearish_strategy_count,o.consensus_direction,o.consensus_score,o.conflict_score,o.supporting_strategies,o.opposing_strategies INTO NEW.strategy_count,NEW.bullish_strategy_count,NEW.bearish_strategy_count,NEW.consensus_direction,NEW.consensus_score,NEW.conflict_score,NEW.supporting_strategies,NEW.opposing_strategies FROM public.market_screener_opportunities o WHERE o.opportunity_id=NEW.opportunity_id; RETURN NEW; END $$;


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


--
-- Name: trg_validate_entity_canonical_name(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_validate_entity_canonical_name() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.canonical_name_observation_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.entity_name_observations eno
           WHERE eno.entity_name_observation_id = NEW.canonical_name_observation_id
             AND eno.entity_id = NEW.entity_id
       )
    THEN
        RAISE EXCEPTION
            'Canonical name observation % does not belong to entity %',
            NEW.canonical_name_observation_id,
            NEW.entity_id;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: alert_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_events (
    alert_event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_id uuid NOT NULL,
    user_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    signal_id uuid,
    fingerprint character varying(64) NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    notification_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_rules (
    rule_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(160) NOT NULL,
    alert_type character varying(40) DEFAULT 'MARKET_SIGNAL'::character varying NOT NULL,
    severity character varying(20) DEFAULT 'MEDIUM'::character varying NOT NULL,
    channel character varying(30),
    conditions jsonb DEFAULT '{}'::jsonb NOT NULL,
    cooldown_minutes integer DEFAULT 240 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT alert_rules_alert_type_check CHECK (alert_type IN ('MARKET_SIGNAL', 'FIXED_PRICE', 'PERCENT_MOVE', 'SIGNAL_CHANGE')),
    CONSTRAINT alert_rules_cooldown_minutes_check CHECK ((cooldown_minutes >= 0)),
    CONSTRAINT alert_rules_severity_check CHECK (((severity)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'CRITICAL'::character varying])::text[])))
);


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
    raw_payload jsonb,
    catalyst_day date NOT NULL
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
    updated_at timestamp with time zone DEFAULT now(),
    canonical_name_observation_id uuid,
    name_locked boolean DEFAULT false NOT NULL
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
-- Name: entity_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_identities (
    entity_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
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
-- Name: entity_name_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_name_observations (
    entity_name_observation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_id uuid NOT NULL,
    system_id uuid NOT NULL,
    observed_name character varying(255) NOT NULL,
    normalized_name text GENERATED ALWAYS AS (public.sp_normalize_entity_name((observed_name)::text)) STORED,
    source_reference character varying(255),
    normalized_source_reference text GENERATED ALWAYS AS (COALESCE(btrim((source_reference)::text), ''::text)) STORED,
    confidence numeric(5,4),
    first_observed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_observed_at timestamp with time zone DEFAULT now() NOT NULL,
    observation_count integer DEFAULT 1 NOT NULL,
    raw_payload jsonb,
    is_preferred boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_entity_name_observations_confidence CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT ck_entity_name_observations_count CHECK ((observation_count > 0)),
    CONSTRAINT ck_entity_name_observations_dates CHECK ((last_observed_at >= first_observed_at)),
    CONSTRAINT ck_entity_name_observations_name CHECK ((btrim((observed_name)::text) <> ''::text)),
    CONSTRAINT ck_entity_name_observations_normalized_name CHECK (((normalized_name IS NOT NULL) AND (btrim(normalized_name) <> ''::text)))
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
-- Name: exchange_holiday_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_holiday_snapshots (
    snapshot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    exchange character varying(20) NOT NULL,
    exchange_code character varying(20) NOT NULL,
    source text NOT NULL,
    provider_exchange text NOT NULL,
    as_of date NOT NULL,
    provider_updated_at timestamp with time zone NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    raw_payload jsonb NOT NULL,
    CONSTRAINT exchange_holiday_snapshots_exchange_code_check CHECK (((exchange_code)::text ~ '^[A-Z0-9]{4,20}$'::text)),
    CONSTRAINT exchange_holiday_snapshots_raw_payload_check CHECK ((jsonb_typeof(raw_payload) = 'object'::text))
);


--
-- Name: exchange_rate_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_rate_history (
    currency_code character(3) NOT NULL,
    rate_date date NOT NULL,
    rate_to_usd numeric(18,8) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_exchange_rate_history_currency CHECK ((((currency_code)::text = upper((currency_code)::text)) AND (currency_code ~ '^[A-Z]{3}$'::text))),
    CONSTRAINT ck_exchange_rate_history_rate CHECK ((rate_to_usd > (0)::numeric))
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
-- Name: exchange_source_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_source_mappings (
    exchange character varying(20) NOT NULL,
    system_id uuid NOT NULL,
    source_exchange_code character varying(100) NOT NULL,
    source_exchange_name character varying(255),
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_payload jsonb
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
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    calendar_name character varying(64)
);


--
-- Name: external_systems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.external_systems (
    system_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100),
    system_type character varying(50),
    code character varying(50),
    base_url text,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
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
-- Name: identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.identities (
    identity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    identity_type_id uuid NOT NULL,
    identity_value character varying(255) NOT NULL,
    normalized_value character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: identity_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.identity_observations (
    identity_observation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    identity_id uuid NOT NULL,
    system_id uuid,
    source_reference character varying(255),
    confidence numeric(5,4),
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone,
    raw_payload jsonb,
    CONSTRAINT ck_identity_observations_confidence CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))))
);


--
-- Name: identity_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.identity_types (
    identity_type_id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(50) NOT NULL,
    target_kind character varying(20) NOT NULL,
    description text,
    value_pattern text,
    case_sensitive boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_identity_types_target_kind CHECK (((target_kind)::text = ANY ((ARRAY['ENTITY'::character varying, 'INSTRUMENT'::character varying])::text[])))
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
-- Name: instrument_consolidation_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instrument_consolidation_audit (
    consolidation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    duplicate_instrument_id uuid NOT NULL,
    canonical_instrument_id uuid NOT NULL,
    duplicate_entity_id uuid NOT NULL,
    canonical_entity_id uuid NOT NULL,
    symbol character varying(50) NOT NULL,
    exchange character varying(50),
    consolidated_at timestamp with time zone DEFAULT now() NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL
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
-- Name: instrument_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instrument_identities (
    instrument_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
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
    updated_at timestamp with time zone DEFAULT now(),
    market_sector character varying(50),
    price_source_id uuid,
    price_as_of timestamp with time zone,
    CONSTRAINT ck_instruments_currency_code_present CHECK ((NULLIF(btrim((currency_code)::text), ''::text) IS NOT NULL))
);


--
-- Name: llm_rate_limit_windows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_rate_limit_windows (
    queue_key character varying(400) NOT NULL,
    window_started_at timestamp with time zone DEFAULT now() NOT NULL,
    tokens_used integer DEFAULT 0 NOT NULL,
    requests_used integer DEFAULT 0 NOT NULL,
    blocked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    quota_day date,
    requests_used_today integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_llm_rate_daily_usage CHECK ((requests_used_today >= 0)),
    CONSTRAINT ck_llm_rate_usage CHECK (((tokens_used >= 0) AND (requests_used >= 0)))
);


--
-- Name: llm_request_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_request_queue (
    request_id uuid DEFAULT gen_random_uuid() NOT NULL,
    queue_key character varying(400) NOT NULL,
    provider character varying(64) NOT NULL,
    model character varying(255) NOT NULL,
    request_payload jsonb NOT NULL,
    estimated_tokens integer NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 4 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_at timestamp with time zone,
    locked_by character varying(150),
    completed_at timestamp with time zone,
    response_payload jsonb,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_expires_at timestamp with time zone,
    CONSTRAINT ck_llm_queue_attempts CHECK (((estimated_tokens > 0) AND (attempt_count >= 0) AND (max_attempts > 0))),
    CONSTRAINT ck_llm_queue_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'RETRY'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: llm_response_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_response_cache (
    cache_key character varying(64) NOT NULL,
    provider character varying(50) NOT NULL,
    model character varying(255) NOT NULL,
    status character varying(20) NOT NULL,
    request_payload jsonb NOT NULL,
    response_data jsonb,
    response_raw jsonb,
    sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    search_executed boolean DEFAULT false NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    claimed_at timestamp with time zone,
    last_accessed_at timestamp with time zone,
    hit_count bigint DEFAULT 0 NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_llm_response_cache_response CHECK ((((status)::text <> 'READY'::text) OR ((response_data IS NOT NULL) AND (response_raw IS NOT NULL)))),
    CONSTRAINT ck_llm_response_cache_status CHECK (((status)::text = ANY ((ARRAY['PROCESSING'::character varying, 'READY'::character varying])::text[])))
);


--
-- Name: market_analysis_scan_failures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_analysis_scan_failures (
    failure_id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    symbol character varying(50) NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: market_analysis_scan_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_analysis_scan_runs (
    run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    universe_id uuid NOT NULL,
    status character varying(20) DEFAULT 'RUNNING'::character varying NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_analysis_scan_runs_status CHECK (((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: market_analysis_signal_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_analysis_signal_events (
    signal_event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    signal_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    strategy_code character varying NOT NULL,
    timeframe character varying NOT NULL,
    previous_status character varying,
    new_status character varying NOT NULL,
    previous_direction character varying,
    new_direction character varying NOT NULL,
    event_type character varying NOT NULL,
    reason character varying,
    observed_at timestamp with time zone NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: market_discovery_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_discovery_candidates (
    candidate_id uuid DEFAULT gen_random_uuid() NOT NULL,
    snapshot_id uuid NOT NULL,
    snapshot_item_id uuid NOT NULL,
    entity_id uuid,
    instrument_id uuid,
    symbol character varying(50) NOT NULL,
    origin character varying(20),
    resolution_status character varying(20) NOT NULL,
    attention_score numeric(7,4) NOT NULL,
    universe_codes jsonb DEFAULT '[]'::jsonb NOT NULL,
    attention_evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_candidates_origin CHECK (((origin IS NULL) OR ((origin)::text = ANY ((ARRAY['CORE'::character varying, 'DISCOVERY'::character varying])::text[])))),
    CONSTRAINT ck_market_discovery_candidates_resolution CHECK (((resolution_status)::text = ANY ((ARRAY['RESOLVED'::character varying, 'AMBIGUOUS'::character varying, 'UNRESOLVED'::character varying, 'REJECTED'::character varying])::text[]))),
    CONSTRAINT ck_market_discovery_candidates_resolution_values CHECK (((((resolution_status)::text = 'RESOLVED'::text) AND (entity_id IS NOT NULL) AND (instrument_id IS NOT NULL) AND (origin IS NOT NULL)) OR (((resolution_status)::text <> 'RESOLVED'::text) AND (origin IS NULL)))),
    CONSTRAINT ck_market_discovery_candidates_score CHECK (((attention_score >= (0)::numeric) AND (attention_score <= (100)::numeric))),
    CONSTRAINT ck_market_discovery_candidates_symbol CHECK ((btrim((symbol)::text) <> ''::text))
);


--
-- Name: market_discovery_snapshot_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_discovery_snapshot_items (
    snapshot_item_id uuid DEFAULT gen_random_uuid() NOT NULL,
    snapshot_id uuid NOT NULL,
    symbol character varying(50) NOT NULL,
    name character varying(255),
    rank integer,
    previous_rank integer,
    mentions integer,
    previous_mentions integer,
    upvotes integer,
    score numeric,
    raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_items_mentions CHECK (((mentions IS NULL) OR (mentions >= 0))),
    CONSTRAINT ck_market_discovery_items_previous_mentions CHECK (((previous_mentions IS NULL) OR (previous_mentions >= 0))),
    CONSTRAINT ck_market_discovery_items_previous_rank CHECK (((previous_rank IS NULL) OR (previous_rank > 0))),
    CONSTRAINT ck_market_discovery_items_rank CHECK (((rank IS NULL) OR (rank > 0))),
    CONSTRAINT ck_market_discovery_items_symbol CHECK ((btrim((symbol)::text) <> ''::text)),
    CONSTRAINT ck_market_discovery_items_upvotes CHECK (((upvotes IS NULL) OR (upvotes >= 0)))
);


--
-- Name: market_discovery_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_discovery_snapshots (
    snapshot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_id uuid NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    requested_limit integer NOT NULL,
    returned_count integer NOT NULL,
    source_reference text,
    source_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_snapshots_count CHECK ((returned_count >= 0)),
    CONSTRAINT ck_market_discovery_snapshots_limit CHECK ((requested_limit > 0))
);


--
-- Name: market_discovery_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_discovery_sources (
    source_id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_code character varying(50) NOT NULL,
    source_type character varying(50) NOT NULL,
    name character varying(255) NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_sources_name CHECK ((btrim((name)::text) <> ''::text)),
    CONSTRAINT ck_market_discovery_sources_provider CHECK ((btrim((provider_code)::text) <> ''::text)),
    CONSTRAINT ck_market_discovery_sources_type CHECK ((btrim((source_type)::text) <> ''::text))
);


--
-- Name: market_price_refresh_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_price_refresh_queue (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    instrument_id uuid NOT NULL,
    symbol character varying(50) NOT NULL,
    data_source character varying(50) DEFAULT 'YAHOO'::character varying NOT NULL,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 6 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_at timestamp with time zone,
    locked_by character varying(150),
    last_error text,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_price_refresh_attempts CHECK (((attempt_count >= 0) AND (max_attempts > 0))),
    CONSTRAINT ck_market_price_refresh_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'RETRY'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: market_screener_opportunities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_screener_opportunities (
    opportunity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    signal_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    symbol character varying(50) NOT NULL,
    direction character varying(20) NOT NULL,
    origin character varying(30),
    attention_score numeric(12,4),
    technical_score numeric(12,4),
    quant_score numeric(12,4),
    llm_score numeric(12,4),
    composite_score numeric(12,4) NOT NULL,
    ranking_position integer NOT NULL,
    verdict character varying(30),
    summary text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    entity_id uuid NOT NULL,
    strategy_code character varying,
    signal_type character varying,
    signal_timeframe character varying,
    signal_trend character varying,
    signal_confidence numeric,
    reference_price numeric,
    invalidation_price numeric,
    target_price numeric,
    signal_reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    technical_evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    strategy_count integer DEFAULT 1 NOT NULL,
    bullish_strategy_count integer DEFAULT 0 NOT NULL,
    bearish_strategy_count integer DEFAULT 0 NOT NULL,
    consensus_direction character varying DEFAULT 'NEUTRAL'::character varying NOT NULL,
    consensus_score numeric DEFAULT 0 NOT NULL,
    conflict_score numeric DEFAULT 0 NOT NULL,
    supporting_strategies jsonb DEFAULT '[]'::jsonb NOT NULL,
    opposing_strategies jsonb DEFAULT '[]'::jsonb NOT NULL,
    supporting_signal_ids jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: market_screener_ranking_snapshot_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_screener_ranking_snapshot_items (
    ranking_snapshot_item_id uuid DEFAULT gen_random_uuid() CONSTRAINT market_screener_ranking_snaps_ranking_snapshot_item_id_not_null NOT NULL,
    ranking_snapshot_id uuid CONSTRAINT market_screener_ranking_snapshot_i_ranking_snapshot_id_not_null NOT NULL,
    opportunity_id uuid NOT NULL,
    signal_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    symbol character varying NOT NULL,
    direction character varying NOT NULL,
    origin character varying NOT NULL,
    ranking_position integer CONSTRAINT market_screener_ranking_snapshot_item_ranking_position_not_null NOT NULL,
    previous_ranking_position integer,
    ranking_change integer,
    attention_score numeric,
    technical_score numeric,
    quant_score numeric,
    llm_score numeric,
    composite_score numeric NOT NULL,
    verdict character varying,
    summary text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    strategy_code character varying,
    signal_type character varying,
    signal_timeframe character varying,
    signal_trend character varying,
    signal_confidence numeric,
    reference_price numeric,
    invalidation_price numeric,
    target_price numeric,
    signal_reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    technical_evidence jsonb DEFAULT '{}'::jsonb CONSTRAINT market_screener_ranking_snapshot_it_technical_evidence_not_null NOT NULL,
    strategy_count integer DEFAULT 1 NOT NULL,
    bullish_strategy_count integer DEFAULT 0 CONSTRAINT market_screener_ranking_snapsho_bullish_strategy_count_not_null NOT NULL,
    bearish_strategy_count integer DEFAULT 0 CONSTRAINT market_screener_ranking_snapsho_bearish_strategy_count_not_null NOT NULL,
    consensus_direction character varying DEFAULT 'NEUTRAL'::character varying CONSTRAINT market_screener_ranking_snapshot_i_consensus_direction_not_null NOT NULL,
    consensus_score numeric DEFAULT 0 NOT NULL,
    conflict_score numeric DEFAULT 0 NOT NULL,
    supporting_strategies jsonb DEFAULT '[]'::jsonb CONSTRAINT market_screener_ranking_snapshot_supporting_strategies_not_null NOT NULL,
    opposing_strategies jsonb DEFAULT '[]'::jsonb CONSTRAINT market_screener_ranking_snapshot_i_opposing_strategies_not_null NOT NULL,
    CONSTRAINT ck_market_screener_snapshot_rank CHECK ((ranking_position > 0))
);


--
-- Name: market_screener_ranking_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_screener_ranking_snapshots (
    ranking_snapshot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    screener_run_id uuid NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    opportunity_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_screener_snapshot_count CHECK ((opportunity_count >= 0))
);


--
-- Name: market_screener_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_screener_runs (
    screener_run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    status character varying NOT NULL,
    discovery_snapshot_id uuid,
    scan_run_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    phase_results jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_screener_run_status CHECK (((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'SUCCESS'::character varying, 'PARTIAL'::character varying, 'FAILED'::character varying])::text[])))
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
-- Name: market_universe_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_universe_members (
    universe_member_id uuid DEFAULT gen_random_uuid() NOT NULL,
    universe_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    source_reference character varying(255),
    raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_universe_members_validity CHECK (((valid_to IS NULL) OR (valid_to > valid_from)))
);


--
-- Name: market_universes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_universes (
    universe_id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    universe_type character varying(50) NOT NULL,
    source_system_id uuid,
    active boolean DEFAULT true NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_universes_code CHECK ((NULLIF(btrim((code)::text), ''::text) IS NOT NULL)),
    CONSTRAINT ck_market_universes_name CHECK ((NULLIF(btrim((name)::text), ''::text) IS NOT NULL))
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
    notes text,
    cost_basis_amount numeric(20,8),
    cost_basis_currency character(3),
    CONSTRAINT ck_positions_cost_basis CHECK ((((cost_basis_amount IS NULL) AND (cost_basis_currency IS NULL)) OR ((cost_basis_amount IS NOT NULL) AND (cost_basis_amount >= (0)::numeric) AND (cost_basis_currency IS NOT NULL))))
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
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    role_id uuid DEFAULT gen_random_uuid() NOT NULL,
    role_key character varying(50) NOT NULL,
    role_name character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: scheduler_job_run_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduler_job_run_logs (
    log_id bigint NOT NULL,
    job_run_id uuid NOT NULL,
    logged_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    level character varying(20) NOT NULL,
    logger_name character varying(255) NOT NULL,
    message text NOT NULL
);


--
-- Name: scheduler_job_run_logs_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.scheduler_job_run_logs ALTER COLUMN log_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.scheduler_job_run_logs_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
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
-- Name: scheduler_manual_run_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduler_manual_run_requests (
    request_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    requested_by uuid,
    job_run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text,
    CONSTRAINT ck_scheduler_manual_run_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
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
-- Name: system_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_audit_log (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    actor_user_id uuid,
    action character varying(100) NOT NULL,
    resource_type character varying(100) NOT NULL,
    resource_id character varying(255),
    old_value jsonb,
    new_value jsonb,
    created_at timestamp with time zone DEFAULT now()
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
-- Name: trading_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trading_signals (
    signal_id uuid DEFAULT gen_random_uuid() NOT NULL,
    instrument_id uuid NOT NULL,
    universe_id uuid,
    strategy_code character varying(100) NOT NULL,
    timeframe character varying(20) NOT NULL,
    direction character varying(10) NOT NULL,
    signal_type character varying(20) NOT NULL,
    trend character varying(30) NOT NULL,
    status character varying(20) DEFAULT 'CANDIDATE'::character varying NOT NULL,
    confidence numeric(7,6) NOT NULL,
    technical_score numeric(7,3),
    financial_score numeric(7,3),
    catalyst_score numeric(7,3),
    llm_score numeric(7,3),
    composite_score numeric(7,3),
    reference_price numeric(20,8) NOT NULL,
    invalidation_price numeric(20,8),
    target_price numeric(20,8),
    observed_at timestamp with time zone NOT NULL,
    confirmed_at timestamp with time zone,
    expires_at timestamp with time zone,
    closed_at timestamp with time zone,
    close_reason text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    llm_analysis jsonb,
    enrichment_fingerprint character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_trading_signals_confidence CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT ck_trading_signals_direction CHECK (((direction)::text = ANY ((ARRAY['LONG'::character varying, 'SHORT'::character varying])::text[]))),
    CONSTRAINT ck_trading_signals_scores CHECK ((((technical_score IS NULL) OR ((technical_score >= (0)::numeric) AND (technical_score <= (100)::numeric))) AND ((financial_score IS NULL) OR ((financial_score >= (0)::numeric) AND (financial_score <= (100)::numeric))) AND ((catalyst_score IS NULL) OR ((catalyst_score >= (0)::numeric) AND (catalyst_score <= (100)::numeric))) AND ((llm_score IS NULL) OR ((llm_score >= (0)::numeric) AND (llm_score <= (100)::numeric))) AND ((composite_score IS NULL) OR ((composite_score >= (0)::numeric) AND (composite_score <= (100)::numeric))))),
    CONSTRAINT ck_trading_signals_status CHECK (((status)::text = ANY ((ARRAY['CANDIDATE'::character varying, 'CONFIRMED'::character varying, 'INVALIDATED'::character varying, 'EXPIRED'::character varying, 'CLOSED'::character varying])::text[])))
);


--
-- Name: COLUMN trading_signals.enrichment_fingerprint; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.trading_signals.enrichment_fingerprint IS 'SHA-256 fingerprint of the LLM enrichment contract applied to this signal.';


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    user_id uuid NOT NULL,
    role_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now()
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
    reporting_currency character(3) DEFAULT 'EUR'::bpchar NOT NULL,
    timezone character varying(50) DEFAULT 'Europe/Rome'::character varying,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    password_hash text,
    CONSTRAINT ck_users_reporting_currency CHECK ((((reporting_currency)::text = upper((reporting_currency)::text)) AND (reporting_currency ~ '^[A-Z]{3}$'::text)))
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
-- Name: vw_entity_identity_conflicts; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_entity_identity_conflicts AS
 SELECT it.code AS identity_type,
    id.normalized_value AS identity_value,
    count(DISTINCT i.entity_id) AS entity_count,
    array_agg(DISTINCT i.entity_id) AS entity_ids,
    array_agg(DISTINCT e.name ORDER BY e.name) AS entity_names,
    array_agg(DISTINCT i.symbol ORDER BY i.symbol) AS symbols
   FROM ((((public.identities id
     JOIN public.identity_types it ON ((it.identity_type_id = id.identity_type_id)))
     JOIN public.instrument_identities ii ON ((ii.identity_id = id.identity_id)))
     JOIN public.instruments i ON ((i.instrument_id = ii.instrument_id)))
     JOIN public.entities e ON ((e.entity_id = i.entity_id)))
  WHERE (((it.target_kind)::text = 'INSTRUMENT'::text) AND ((it.code)::text = ANY ((ARRAY['ISIN'::character varying, 'FIGI_SHARE_CLASS'::character varying])::text[])))
  GROUP BY it.code, id.normalized_value
 HAVING (count(DISTINCT i.entity_id) > 1);


--
-- Name: vw_entity_name_candidates; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_entity_name_candidates AS
 SELECT eno.normalized_name,
    e.entity_type,
    count(DISTINCT e.entity_id) AS entity_count,
    array_agg(DISTINCT e.entity_id) AS entity_ids,
    array_agg(DISTINCT e.name ORDER BY e.name) AS canonical_names,
    array_agg(DISTINCT eno.observed_name ORDER BY eno.observed_name) AS observed_names
   FROM (public.entity_name_observations eno
     JOIN public.entities e ON ((e.entity_id = eno.entity_id)))
  GROUP BY eno.normalized_name, e.entity_type
 HAVING (count(DISTINCT e.entity_id) > 1);


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
-- Name: alert_events alert_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT alert_events_pkey PRIMARY KEY (alert_event_id);


--
-- Name: alert_rules alert_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_pkey PRIMARY KEY (rule_id);


--
-- Name: alert_rules alert_rules_user_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_user_id_name_key UNIQUE (user_id, name);


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
-- Name: entity_identities entity_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_identities
    ADD CONSTRAINT entity_identities_pkey PRIMARY KEY (entity_id, identity_id);


--
-- Name: entity_metrics entity_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_metrics
    ADD CONSTRAINT entity_metrics_pkey PRIMARY KEY (metric_id);


--
-- Name: entity_name_observations entity_name_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_name_observations
    ADD CONSTRAINT entity_name_observations_pkey PRIMARY KEY (entity_name_observation_id);


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
-- Name: exchange_holiday_snapshots exchange_holiday_snapshots_exchange_code_source_provider_up_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_holiday_snapshots
    ADD CONSTRAINT exchange_holiday_snapshots_exchange_code_source_provider_up_key UNIQUE (exchange_code, source, provider_updated_at);


--
-- Name: exchange_holiday_snapshots exchange_holiday_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_holiday_snapshots
    ADD CONSTRAINT exchange_holiday_snapshots_pkey PRIMARY KEY (snapshot_id);


--
-- Name: exchange_rates exchange_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT exchange_rates_pkey PRIMARY KEY (currency_code);


--
-- Name: exchange_source_mappings exchange_source_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_source_mappings
    ADD CONSTRAINT exchange_source_mappings_pkey PRIMARY KEY (exchange, system_id, source_exchange_code);


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
-- Name: identities identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT identities_pkey PRIMARY KEY (identity_id);


--
-- Name: identity_observations identity_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identity_observations
    ADD CONSTRAINT identity_observations_pkey PRIMARY KEY (identity_observation_id);


--
-- Name: identity_types identity_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identity_types
    ADD CONSTRAINT identity_types_pkey PRIMARY KEY (identity_type_id);


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
-- Name: instrument_consolidation_audit instrument_consolidation_audit_duplicate_instrument_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_consolidation_audit
    ADD CONSTRAINT instrument_consolidation_audit_duplicate_instrument_id_key UNIQUE (duplicate_instrument_id);


--
-- Name: instrument_consolidation_audit instrument_consolidation_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_consolidation_audit
    ADD CONSTRAINT instrument_consolidation_audit_pkey PRIMARY KEY (consolidation_id);


--
-- Name: instrument_entity_exposure instrument_exposure_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_entity_exposure
    ADD CONSTRAINT instrument_exposure_pkey PRIMARY KEY (exposure_id);


--
-- Name: instrument_identities instrument_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_identities
    ADD CONSTRAINT instrument_identities_pkey PRIMARY KEY (instrument_id, identity_id);


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
-- Name: llm_rate_limit_windows llm_rate_limit_windows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_rate_limit_windows
    ADD CONSTRAINT llm_rate_limit_windows_pkey PRIMARY KEY (queue_key);


--
-- Name: llm_request_queue llm_request_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_request_queue
    ADD CONSTRAINT llm_request_queue_pkey PRIMARY KEY (request_id);


--
-- Name: llm_response_cache llm_response_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_response_cache
    ADD CONSTRAINT llm_response_cache_pkey PRIMARY KEY (cache_key);


--
-- Name: market_analysis_scan_failures market_analysis_scan_failures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_scan_failures
    ADD CONSTRAINT market_analysis_scan_failures_pkey PRIMARY KEY (failure_id);


--
-- Name: market_analysis_scan_runs market_analysis_scan_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_scan_runs
    ADD CONSTRAINT market_analysis_scan_runs_pkey PRIMARY KEY (run_id);


--
-- Name: market_analysis_signal_events market_analysis_signal_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_signal_events
    ADD CONSTRAINT market_analysis_signal_events_pkey PRIMARY KEY (signal_event_id);


--
-- Name: market_discovery_candidates market_discovery_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT market_discovery_candidates_pkey PRIMARY KEY (candidate_id);


--
-- Name: market_discovery_snapshot_items market_discovery_snapshot_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshot_items
    ADD CONSTRAINT market_discovery_snapshot_items_pkey PRIMARY KEY (snapshot_item_id);


--
-- Name: market_discovery_snapshots market_discovery_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshots
    ADD CONSTRAINT market_discovery_snapshots_pkey PRIMARY KEY (snapshot_id);


--
-- Name: market_discovery_sources market_discovery_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_sources
    ADD CONSTRAINT market_discovery_sources_pkey PRIMARY KEY (source_id);


--
-- Name: market_price_refresh_queue market_price_refresh_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT market_price_refresh_queue_pkey PRIMARY KEY (job_id);


--
-- Name: market_screener_opportunities market_screener_opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_opportunities
    ADD CONSTRAINT market_screener_opportunities_pkey PRIMARY KEY (opportunity_id);


--
-- Name: market_screener_ranking_snapshot_items market_screener_ranking_snapshot_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshot_items
    ADD CONSTRAINT market_screener_ranking_snapshot_items_pkey PRIMARY KEY (ranking_snapshot_item_id);


--
-- Name: market_screener_ranking_snapshots market_screener_ranking_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshots
    ADD CONSTRAINT market_screener_ranking_snapshots_pkey PRIMARY KEY (ranking_snapshot_id);


--
-- Name: market_screener_runs market_screener_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_runs
    ADD CONSTRAINT market_screener_runs_pkey PRIMARY KEY (screener_run_id);


--
-- Name: market_search_cache market_search_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_search_cache
    ADD CONSTRAINT market_search_cache_pkey PRIMARY KEY (cache_key);


--
-- Name: market_universe_members market_universe_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_universe_members
    ADD CONSTRAINT market_universe_members_pkey PRIMARY KEY (universe_member_id);


--
-- Name: market_universes market_universes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_universes
    ADD CONSTRAINT market_universes_pkey PRIMARY KEY (universe_id);


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
-- Name: exchange_rate_history pk_exchange_rate_history; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_rate_history
    ADD CONSTRAINT pk_exchange_rate_history PRIMARY KEY (currency_code, rate_date);


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
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (role_id);


--
-- Name: roles roles_role_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_role_key_key UNIQUE (role_key);


--
-- Name: scheduler_job_run_logs scheduler_job_run_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_job_run_logs
    ADD CONSTRAINT scheduler_job_run_logs_pkey PRIMARY KEY (log_id);


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
-- Name: scheduler_manual_run_requests scheduler_manual_run_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_manual_run_requests
    ADD CONSTRAINT scheduler_manual_run_requests_pkey PRIMARY KEY (request_id);


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
-- Name: system_audit_log system_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_audit_log
    ADD CONSTRAINT system_audit_log_pkey PRIMARY KEY (audit_id);


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
-- Name: trading_signals trading_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_signals
    ADD CONSTRAINT trading_signals_pkey PRIMARY KEY (signal_id);


--
-- Name: identities uq_identities_type_value; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT uq_identities_type_value UNIQUE (identity_type_id, normalized_value);


--
-- Name: market_discovery_candidates uq_market_discovery_candidates_snapshot_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT uq_market_discovery_candidates_snapshot_item UNIQUE (snapshot_item_id);


--
-- Name: market_discovery_candidates uq_market_discovery_candidates_snapshot_symbol; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT uq_market_discovery_candidates_snapshot_symbol UNIQUE (snapshot_id, symbol);


--
-- Name: market_discovery_snapshot_items uq_market_discovery_items_rank; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshot_items
    ADD CONSTRAINT uq_market_discovery_items_rank UNIQUE (snapshot_id, rank);


--
-- Name: market_discovery_snapshot_items uq_market_discovery_items_symbol; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshot_items
    ADD CONSTRAINT uq_market_discovery_items_symbol UNIQUE (snapshot_id, symbol);


--
-- Name: market_discovery_sources uq_market_discovery_sources; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_sources
    ADD CONSTRAINT uq_market_discovery_sources UNIQUE (provider_code, source_type);


--
-- Name: market_price_refresh_queue uq_market_price_refresh_instrument; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT uq_market_price_refresh_instrument UNIQUE (instrument_id);


--
-- Name: market_screener_ranking_snapshot_items uq_market_screener_snapshot_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshot_items
    ADD CONSTRAINT uq_market_screener_snapshot_item UNIQUE (ranking_snapshot_id, opportunity_id);


--
-- Name: market_screener_ranking_snapshots uq_market_screener_snapshot_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshots
    ADD CONSTRAINT uq_market_screener_snapshot_run UNIQUE (screener_run_id);


--
-- Name: scheduler_manual_run_requests uq_scheduler_manual_run_job_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_manual_run_requests
    ADD CONSTRAINT uq_scheduler_manual_run_job_run UNIQUE (job_run_id);


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
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id);


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
-- Name: ix_alert_events_rule_instrument_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_rule_instrument_created ON public.alert_events USING btree (rule_id, instrument_id, created_at DESC);


--
-- Name: ix_entities_normalized_name_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entities_normalized_name_type ON public.entities USING btree (public.sp_normalize_entity_name((name)::text), entity_type, country_code) WHERE (public.sp_normalize_entity_name((name)::text) IS NOT NULL);


--
-- Name: ix_entity_alias_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_alias_normalized ON public.entity_aliases USING btree (public.sp_normalize_entity_name((alias_name)::text), entity_id) WHERE ((alias_name IS NOT NULL) AND (btrim((alias_name)::text) <> ''::text));


--
-- Name: ix_entity_alias_normalized_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_alias_normalized_lookup ON public.entity_aliases USING btree (public.sp_normalize_entity_name((alias_name)::text)) WHERE (public.sp_normalize_entity_name((alias_name)::text) IS NOT NULL);


--
-- Name: ix_entity_identities_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_identities_identity ON public.entity_identities USING btree (identity_id, entity_id);


--
-- Name: ix_entity_name_observation_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_name_observation_entity ON public.entity_name_observations USING btree (entity_id, last_observed_at DESC);


--
-- Name: ix_entity_name_observation_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_name_observation_normalized ON public.entity_name_observations USING btree (normalized_name, entity_id);


--
-- Name: ix_entity_name_observation_system; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_name_observation_system ON public.entity_name_observations USING btree (system_id, last_observed_at DESC);


--
-- Name: ix_entity_name_observations_normalized_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_name_observations_normalized_lookup ON public.entity_name_observations USING btree (normalized_name, entity_id) WHERE (normalized_name IS NOT NULL);


--
-- Name: ix_entity_name_observations_source_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_name_observations_source_entity ON public.entity_name_observations USING btree (system_id, entity_id, last_observed_at DESC);


--
-- Name: ix_exchange_rate_history_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exchange_rate_history_date ON public.exchange_rate_history USING btree (rate_date DESC, currency_code);


--
-- Name: ix_exchange_source_mappings_exchange; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exchange_source_mappings_exchange ON public.exchange_source_mappings USING btree (exchange);


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
-- Name: ix_identities_normalized_value; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_identities_normalized_value ON public.identities USING btree (normalized_value);


--
-- Name: ix_identity_observations_system_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_identity_observations_system_identity ON public.identity_observations USING btree (system_id, identity_id, observed_at DESC) WHERE (system_id IS NOT NULL);


--
-- Name: ix_identity_types_kind_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_identity_types_kind_code ON public.identity_types USING btree (target_kind, code);


--
-- Name: ix_instrument_alias_normalized_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instrument_alias_normalized_lookup ON public.instrument_aliases USING btree (upper(btrim((alias_symbol)::text))) WHERE (NULLIF(btrim((alias_symbol)::text), ''::text) IS NOT NULL);


--
-- Name: ix_instrument_identities_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instrument_identities_identity ON public.instrument_identities USING btree (identity_id);


--
-- Name: ix_instrument_identities_identity_instrument; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instrument_identities_identity_instrument ON public.instrument_identities USING btree (identity_id, instrument_id);


--
-- Name: ix_instruments_entity_normalized_listing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instruments_entity_normalized_listing ON public.instruments USING btree (entity_id, upper(btrim((symbol)::text)), COALESCE(upper(btrim((exchange)::text)), ''::text), COALESCE(upper(btrim((currency_code)::text)), ''::text)) WHERE ((symbol IS NOT NULL) AND (btrim((symbol)::text) <> ''::text));


--
-- Name: ix_instruments_missing_reference_metadata; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instruments_missing_reference_metadata ON public.instruments USING btree (instrument_id, symbol) WHERE ((exchange IS NULL) OR (currency_code IS NULL) OR (instrument_type IS NULL));


--
-- Name: ix_instruments_normalized_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instruments_normalized_symbol ON public.instruments USING btree (upper(btrim((symbol)::text))) WHERE ((symbol IS NOT NULL) AND (btrim((symbol)::text) <> ''::text));


--
-- Name: ix_instruments_symbol_exchange; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_instruments_symbol_exchange ON public.instruments USING btree (symbol, exchange);


--
-- Name: ix_llm_queue_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_queue_due ON public.llm_request_queue USING btree (queue_key, status, available_at, priority, created_at) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RETRY'::character varying])::text[]));


--
-- Name: ix_llm_queue_expired_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_queue_expired_processing ON public.llm_request_queue USING btree (lease_expires_at) WHERE ((status)::text = 'PROCESSING'::text);


--
-- Name: ix_llm_queue_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_queue_processing ON public.llm_request_queue USING btree (status, locked_at) WHERE ((status)::text = 'PROCESSING'::text);


--
-- Name: ix_llm_request_queue_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_request_queue_cleanup ON public.llm_request_queue USING btree (status, completed_at, created_at);


--
-- Name: ix_llm_response_cache_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_response_cache_expiry ON public.llm_response_cache USING btree (expires_at);


--
-- Name: ix_llm_response_cache_provider_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_response_cache_provider_model ON public.llm_response_cache USING btree (provider, model);


--
-- Name: ix_market_analysis_scan_runs_universe_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_analysis_scan_runs_universe_started ON public.market_analysis_scan_runs USING btree (universe_id, started_at DESC);


--
-- Name: ix_market_analysis_signal_events_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_analysis_signal_events_lookup ON public.market_analysis_signal_events USING btree (instrument_id, strategy_code, timeframe, observed_at DESC);


--
-- Name: ix_market_discovery_candidates_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_discovery_candidates_entity ON public.market_discovery_candidates USING btree (entity_id) WHERE (entity_id IS NOT NULL);


--
-- Name: ix_market_discovery_candidates_instrument; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_discovery_candidates_instrument ON public.market_discovery_candidates USING btree (instrument_id) WHERE (instrument_id IS NOT NULL);


--
-- Name: ix_market_discovery_candidates_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_discovery_candidates_rank ON public.market_discovery_candidates USING btree (snapshot_id, resolution_status, attention_score DESC);


--
-- Name: ix_market_discovery_snapshot_items_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_discovery_snapshot_items_symbol ON public.market_discovery_snapshot_items USING btree (symbol, snapshot_id);


--
-- Name: ix_market_discovery_snapshots_latest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_discovery_snapshots_latest ON public.market_discovery_snapshots USING btree (source_id, observed_at DESC, snapshot_id DESC);


--
-- Name: ix_market_price_refresh_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_price_refresh_due ON public.market_price_refresh_queue USING btree (status, next_attempt_at) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RETRY'::character varying])::text[]));


--
-- Name: ix_market_screener_opportunities_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_screener_opportunities_entity ON public.market_screener_opportunities USING btree (entity_id, active, ranking_position);


--
-- Name: ix_market_screener_runs_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_screener_runs_started ON public.market_screener_runs USING btree (started_at DESC);


--
-- Name: ix_market_screener_snapshot_items_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_screener_snapshot_items_entity ON public.market_screener_ranking_snapshot_items USING btree (entity_id, ranking_snapshot_id);


--
-- Name: ix_market_screener_snapshots_observed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_screener_snapshots_observed ON public.market_screener_ranking_snapshots USING btree (observed_at DESC);


--
-- Name: ix_market_universe_members_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_universe_members_history ON public.market_universe_members USING btree (universe_id, valid_from DESC, valid_to);


--
-- Name: ix_news_entities_entity_news; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_entities_entity_news ON public.news_entities USING btree (entity_id, news_id);


--
-- Name: ix_news_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_published_at ON public.news USING btree (published_at DESC);


--
-- Name: ix_news_source_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_source_published ON public.news USING btree (source_id, published_at DESC);


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
-- Name: ix_scheduler_job_run_logs_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scheduler_job_run_logs_run ON public.scheduler_job_run_logs USING btree (job_run_id, log_id);


--
-- Name: ix_scheduler_manual_run_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scheduler_manual_run_pending ON public.scheduler_manual_run_requests USING btree (status, requested_at);


--
-- Name: ix_sync_mapping_external_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sync_mapping_external_lookup ON public.sync_mapping USING btree (system_id, internal_type, external_id) WHERE ((system_id IS NOT NULL) AND (external_id IS NOT NULL));


--
-- Name: ix_sync_mapping_internal_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sync_mapping_internal_lookup ON public.sync_mapping USING btree (internal_type, internal_id) WHERE (internal_id IS NOT NULL);


--
-- Name: ix_trading_signals_status_observed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trading_signals_status_observed ON public.trading_signals USING btree (status, observed_at DESC);


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
-- Name: uq_alert_events_rule_instrument_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_alert_events_rule_instrument_fingerprint ON public.alert_events USING btree (rule_id, instrument_id, fingerprint);


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
-- Name: uq_catalyst_business_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_catalyst_business_key ON public.catalysts USING btree (entity_id, catalyst_type, source, catalyst_day);


--
-- Name: uq_entity_alias_entity_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_alias_entity_normalized ON public.entity_aliases USING btree (entity_id, public.sp_normalize_entity_name((alias_name)::text)) WHERE ((alias_name IS NOT NULL) AND (btrim((alias_name)::text) <> ''::text));


--
-- Name: uq_entity_alias_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_alias_normalized ON public.entity_aliases USING btree (entity_id, public.sp_normalize_entity_name((alias_name)::text)) WHERE ((entity_id IS NOT NULL) AND (public.sp_normalize_entity_name((alias_name)::text) IS NOT NULL));


--
-- Name: uq_entity_metric; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_metric ON public.entity_metrics USING btree (entity_id, metric_name, metric_date);


--
-- Name: uq_entity_name_observation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_name_observation ON public.entity_name_observations USING btree (entity_id, system_id, observed_name, normalized_source_reference);


--
-- Name: uq_entity_score; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_entity_score ON public.entity_scores USING btree (entity_id, component_id);


--
-- Name: uq_external_systems_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_external_systems_code ON public.external_systems USING btree (code) WHERE (code IS NOT NULL);


--
-- Name: uq_filings; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_filings ON public.filings USING btree (cik, accepted_at, filing_type);


--
-- Name: uq_holding_snapshot; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_holding_snapshot ON public.portfolio_holdings_snapshots USING btree (portfolio_id, instrument_id, snapshot_date);


--
-- Name: uq_identity_observation_source; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_identity_observation_source ON public.identity_observations USING btree (identity_id, system_id, COALESCE(source_reference, ''::character varying));


--
-- Name: uq_identity_types_kind_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_identity_types_kind_code ON public.identity_types USING btree (target_kind, code);


--
-- Name: uq_instrument_alias_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_instrument_alias_normalized ON public.instrument_aliases USING btree (instrument_id, upper(btrim((alias_symbol)::text))) WHERE ((instrument_id IS NOT NULL) AND (NULLIF(btrim((alias_symbol)::text), ''::text) IS NOT NULL));


--
-- Name: uq_instruments_normalized_listing; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_instruments_normalized_listing ON public.instruments USING btree (upper(btrim((symbol)::text)), COALESCE(upper(btrim((exchange)::text)), ''::text), COALESCE(upper(btrim((currency_code)::text)), ''::text)) WHERE ((symbol IS NOT NULL) AND (btrim((symbol)::text) <> ''::text));


--
-- Name: INDEX uq_instruments_normalized_listing; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.uq_instruments_normalized_listing IS 'Prevents duplicate normalized listing rows by symbol, exchange and trading currency; NULL components compare as empty strings.';


--
-- Name: uq_market_universe_members_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_universe_members_active ON public.market_universe_members USING btree (universe_id, instrument_id) WHERE (valid_to IS NULL);


--
-- Name: uq_market_universes_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_universes_code ON public.market_universes USING btree (upper(btrim((code)::text)));


--
-- Name: uq_news_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_news_entity ON public.news_entities USING btree (news_id, entity_id);


--
-- Name: uq_news_impact; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_news_impact ON public.news_impacts USING btree (news_id, entity_id);


--
-- Name: uq_notification_subscriptions_user_event; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_notification_subscriptions_user_event ON public.notification_subscriptions USING btree (user_id, event_type) WHERE (portfolio_id IS NULL);


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
-- Name: uq_trading_signals_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_trading_signals_active ON public.trading_signals USING btree (instrument_id, strategy_code, timeframe, direction) WHERE ((status)::text = ANY ((ARRAY['CANDIDATE'::character varying, 'CONFIRMED'::character varying])::text[]));


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
-- Name: alert_events trg_alert_events_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_events_updated_at BEFORE UPDATE ON public.alert_events FOR EACH ROW EXECUTE FUNCTION public.fn_alert_set_updated_at();


--
-- Name: alert_rules trg_alert_rules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_rules_updated_at BEFORE UPDATE ON public.alert_rules FOR EACH ROW EXECUTE FUNCTION public.fn_alert_set_updated_at();


--
-- Name: entities trg_entities_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entities_created_at BEFORE INSERT ON public.entities FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: entities trg_entities_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entities_updated_at BEFORE INSERT OR UPDATE ON public.entities FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: entities trg_entities_validate_canonical_name; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trg_entities_validate_canonical_name AFTER INSERT OR UPDATE OF canonical_name_observation_id ON public.entities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_validate_entity_canonical_name();


--
-- Name: entity_impact_history trg_entity_impact_history_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entity_impact_history_created_at BEFORE INSERT ON public.entity_impact_history FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: exchange_rate_history trg_exchange_rate_history_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exchange_rate_history_created_at BEFORE INSERT ON public.exchange_rate_history FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: exchange_rate_history trg_exchange_rate_history_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exchange_rate_history_updated_at BEFORE INSERT OR UPDATE ON public.exchange_rate_history FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


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
-- Name: llm_rate_limit_windows trg_llm_rate_limit_windows_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_llm_rate_limit_windows_updated_at BEFORE INSERT OR UPDATE ON public.llm_rate_limit_windows FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: llm_request_queue trg_llm_request_queue_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_llm_request_queue_created_at BEFORE INSERT ON public.llm_request_queue FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: llm_request_queue trg_llm_request_queue_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_llm_request_queue_updated_at BEFORE INSERT OR UPDATE ON public.llm_request_queue FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: llm_response_cache trg_llm_response_cache_timestamps; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_llm_response_cache_timestamps BEFORE INSERT OR UPDATE ON public.llm_response_cache FOR EACH ROW EXECUTE FUNCTION public.trg_llm_response_cache_timestamps();


--
-- Name: market_analysis_scan_failures trg_market_analysis_scan_failures_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_scan_failures_created_at BEFORE INSERT ON public.market_analysis_scan_failures FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_analysis_scan_failures trg_market_analysis_scan_failures_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_scan_failures_updated_at BEFORE INSERT OR UPDATE ON public.market_analysis_scan_failures FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_analysis_scan_runs trg_market_analysis_scan_runs_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_scan_runs_created_at BEFORE INSERT ON public.market_analysis_scan_runs FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_analysis_scan_runs trg_market_analysis_scan_runs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_scan_runs_updated_at BEFORE INSERT OR UPDATE ON public.market_analysis_scan_runs FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_analysis_signal_events trg_market_analysis_signal_events_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_signal_events_created_at BEFORE INSERT ON public.market_analysis_signal_events FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_analysis_signal_events trg_market_analysis_signal_events_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_analysis_signal_events_updated_at BEFORE INSERT OR UPDATE ON public.market_analysis_signal_events FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_discovery_candidates trg_market_discovery_candidates_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_candidates_created_at BEFORE INSERT ON public.market_discovery_candidates FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_discovery_candidates trg_market_discovery_candidates_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_candidates_updated_at BEFORE INSERT OR UPDATE ON public.market_discovery_candidates FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_discovery_snapshot_items trg_market_discovery_snapshot_items_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_snapshot_items_created_at BEFORE INSERT ON public.market_discovery_snapshot_items FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_discovery_snapshot_items trg_market_discovery_snapshot_items_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_snapshot_items_updated_at BEFORE INSERT OR UPDATE ON public.market_discovery_snapshot_items FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_discovery_snapshots trg_market_discovery_snapshots_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_snapshots_created_at BEFORE INSERT ON public.market_discovery_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_discovery_snapshots trg_market_discovery_snapshots_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_snapshots_updated_at BEFORE INSERT OR UPDATE ON public.market_discovery_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_discovery_sources trg_market_discovery_sources_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_sources_created_at BEFORE INSERT ON public.market_discovery_sources FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_discovery_sources trg_market_discovery_sources_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_discovery_sources_updated_at BEFORE INSERT OR UPDATE ON public.market_discovery_sources FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_screener_opportunities trg_market_screener_opportunities_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_opportunities_updated_at BEFORE UPDATE ON public.market_screener_opportunities FOR EACH ROW EXECUTE FUNCTION public.fn_market_screener_touch_updated_at();


--
-- Name: market_screener_opportunities trg_market_screener_populate_signal_fields; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_populate_signal_fields BEFORE INSERT OR UPDATE OF signal_id ON public.market_screener_opportunities FOR EACH ROW EXECUTE FUNCTION public.trg_market_screener_populate_signal_fields();


--
-- Name: market_screener_runs trg_market_screener_runs_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_runs_created_at BEFORE INSERT ON public.market_screener_runs FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_screener_runs trg_market_screener_runs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_runs_updated_at BEFORE INSERT OR UPDATE ON public.market_screener_runs FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_screener_opportunities trg_market_screener_set_entity_id; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_set_entity_id BEFORE INSERT OR UPDATE OF instrument_id ON public.market_screener_opportunities FOR EACH ROW EXECUTE FUNCTION public.trg_market_screener_set_entity_id();


--
-- Name: market_screener_ranking_snapshot_items trg_market_screener_snapshot_consensus; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_snapshot_consensus BEFORE INSERT OR UPDATE OF opportunity_id ON public.market_screener_ranking_snapshot_items FOR EACH ROW EXECUTE FUNCTION public.trg_market_screener_snapshot_consensus();


--
-- Name: market_screener_ranking_snapshot_items trg_market_screener_snapshot_items_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_snapshot_items_created_at BEFORE INSERT ON public.market_screener_ranking_snapshot_items FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_screener_ranking_snapshot_items trg_market_screener_snapshot_items_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_snapshot_items_updated_at BEFORE INSERT OR UPDATE ON public.market_screener_ranking_snapshot_items FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_screener_ranking_snapshots trg_market_screener_snapshots_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_snapshots_created_at BEFORE INSERT ON public.market_screener_ranking_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_screener_ranking_snapshots trg_market_screener_snapshots_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_screener_snapshots_updated_at BEFORE INSERT OR UPDATE ON public.market_screener_ranking_snapshots FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_universe_members trg_market_universe_members_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_universe_members_created_at BEFORE INSERT ON public.market_universe_members FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_universe_members trg_market_universe_members_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_universe_members_updated_at BEFORE INSERT OR UPDATE ON public.market_universe_members FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: market_universes trg_market_universes_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_universes_created_at BEFORE INSERT ON public.market_universes FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: market_universes trg_market_universes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_market_universes_updated_at BEFORE INSERT OR UPDATE ON public.market_universes FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


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
-- Name: trading_signals trg_trading_signals_created_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_trading_signals_created_at BEFORE INSERT ON public.trading_signals FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();


--
-- Name: trading_signals trg_trading_signals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_trading_signals_updated_at BEFORE INSERT OR UPDATE ON public.trading_signals FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


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
-- Name: alert_events alert_events_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT alert_events_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.alert_rules(rule_id) ON DELETE CASCADE;


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
-- Name: entities entities_canonical_name_observation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_canonical_name_observation_id_fkey FOREIGN KEY (canonical_name_observation_id) REFERENCES public.entity_name_observations(entity_name_observation_id) ON DELETE SET NULL;


--
-- Name: entity_aliases entity_aliases_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_aliases
    ADD CONSTRAINT entity_aliases_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_identities entity_identities_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_identities
    ADD CONSTRAINT entity_identities_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_identities entity_identities_identity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_identities
    ADD CONSTRAINT entity_identities_identity_id_fkey FOREIGN KEY (identity_id) REFERENCES public.identities(identity_id) ON DELETE CASCADE;


--
-- Name: entity_metrics entity_metrics_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_metrics
    ADD CONSTRAINT entity_metrics_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: entity_name_observations entity_name_observations_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_name_observations
    ADD CONSTRAINT entity_name_observations_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE CASCADE;


--
-- Name: entity_name_observations entity_name_observations_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_name_observations
    ADD CONSTRAINT entity_name_observations_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id) ON DELETE RESTRICT;


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
-- Name: exchange_holiday_snapshots exchange_holiday_snapshots_exchange_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_holiday_snapshots
    ADD CONSTRAINT exchange_holiday_snapshots_exchange_fkey FOREIGN KEY (exchange) REFERENCES public.exchanges(exchange);


--
-- Name: exchange_source_mappings exchange_source_mappings_exchange_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_source_mappings
    ADD CONSTRAINT exchange_source_mappings_exchange_fkey FOREIGN KEY (exchange) REFERENCES public.exchanges(exchange) ON DELETE CASCADE;


--
-- Name: exchange_source_mappings exchange_source_mappings_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_source_mappings
    ADD CONSTRAINT exchange_source_mappings_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id) ON DELETE CASCADE;


--
-- Name: filings filings_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filings
    ADD CONSTRAINT filings_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: market_screener_opportunities fk_market_screener_opportunity_entity; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_opportunities
    ADD CONSTRAINT fk_market_screener_opportunity_entity FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id);


--
-- Name: identities identities_identity_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT identities_identity_type_id_fkey FOREIGN KEY (identity_type_id) REFERENCES public.identity_types(identity_type_id);


--
-- Name: identity_observations identity_observations_identity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identity_observations
    ADD CONSTRAINT identity_observations_identity_id_fkey FOREIGN KEY (identity_id) REFERENCES public.identities(identity_id) ON DELETE CASCADE;


--
-- Name: identity_observations identity_observations_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identity_observations
    ADD CONSTRAINT identity_observations_system_id_fkey FOREIGN KEY (system_id) REFERENCES public.external_systems(system_id) ON DELETE SET NULL;


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
-- Name: instrument_identities instrument_identities_identity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_identities
    ADD CONSTRAINT instrument_identities_identity_id_fkey FOREIGN KEY (identity_id) REFERENCES public.identities(identity_id) ON DELETE CASCADE;


--
-- Name: instrument_identities instrument_identities_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instrument_identities
    ADD CONSTRAINT instrument_identities_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE CASCADE;


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
-- Name: instruments instruments_price_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_price_source_id_fkey FOREIGN KEY (price_source_id) REFERENCES public.external_systems(system_id);


--
-- Name: market_analysis_scan_failures market_analysis_scan_failures_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_scan_failures
    ADD CONSTRAINT market_analysis_scan_failures_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: market_analysis_scan_failures market_analysis_scan_failures_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_scan_failures
    ADD CONSTRAINT market_analysis_scan_failures_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.market_analysis_scan_runs(run_id) ON DELETE CASCADE;


--
-- Name: market_analysis_scan_runs market_analysis_scan_runs_universe_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_scan_runs
    ADD CONSTRAINT market_analysis_scan_runs_universe_id_fkey FOREIGN KEY (universe_id) REFERENCES public.market_universes(universe_id);


--
-- Name: market_analysis_signal_events market_analysis_signal_events_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_analysis_signal_events
    ADD CONSTRAINT market_analysis_signal_events_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES public.trading_signals(signal_id) ON DELETE CASCADE;


--
-- Name: market_discovery_candidates market_discovery_candidates_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT market_discovery_candidates_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id) ON DELETE RESTRICT;


--
-- Name: market_discovery_candidates market_discovery_candidates_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT market_discovery_candidates_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT;


--
-- Name: market_discovery_candidates market_discovery_candidates_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT market_discovery_candidates_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.market_discovery_snapshots(snapshot_id) ON DELETE CASCADE;


--
-- Name: market_discovery_candidates market_discovery_candidates_snapshot_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_candidates
    ADD CONSTRAINT market_discovery_candidates_snapshot_item_id_fkey FOREIGN KEY (snapshot_item_id) REFERENCES public.market_discovery_snapshot_items(snapshot_item_id) ON DELETE CASCADE;


--
-- Name: market_discovery_snapshot_items market_discovery_snapshot_items_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshot_items
    ADD CONSTRAINT market_discovery_snapshot_items_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.market_discovery_snapshots(snapshot_id) ON DELETE CASCADE;


--
-- Name: market_discovery_snapshots market_discovery_snapshots_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_discovery_snapshots
    ADD CONSTRAINT market_discovery_snapshots_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.market_discovery_sources(source_id) ON DELETE RESTRICT;


--
-- Name: market_price_refresh_queue market_price_refresh_queue_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT market_price_refresh_queue_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE CASCADE;


--
-- Name: market_screener_opportunities market_screener_opportunities_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_opportunities
    ADD CONSTRAINT market_screener_opportunities_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id);


--
-- Name: market_screener_opportunities market_screener_opportunities_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_opportunities
    ADD CONSTRAINT market_screener_opportunities_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES public.trading_signals(signal_id) ON DELETE CASCADE;


--
-- Name: market_screener_ranking_snapshot_items market_screener_ranking_snapshot_items_ranking_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshot_items
    ADD CONSTRAINT market_screener_ranking_snapshot_items_ranking_snapshot_id_fkey FOREIGN KEY (ranking_snapshot_id) REFERENCES public.market_screener_ranking_snapshots(ranking_snapshot_id) ON DELETE CASCADE;


--
-- Name: market_screener_ranking_snapshots market_screener_ranking_snapshots_screener_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_screener_ranking_snapshots
    ADD CONSTRAINT market_screener_ranking_snapshots_screener_run_id_fkey FOREIGN KEY (screener_run_id) REFERENCES public.market_screener_runs(screener_run_id) ON DELETE CASCADE;


--
-- Name: market_universe_members market_universe_members_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_universe_members
    ADD CONSTRAINT market_universe_members_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT;


--
-- Name: market_universe_members market_universe_members_universe_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_universe_members
    ADD CONSTRAINT market_universe_members_universe_id_fkey FOREIGN KEY (universe_id) REFERENCES public.market_universes(universe_id) ON DELETE CASCADE;


--
-- Name: market_universes market_universes_source_system_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_universes
    ADD CONSTRAINT market_universes_source_system_id_fkey FOREIGN KEY (source_system_id) REFERENCES public.external_systems(system_id);


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
-- Name: scheduler_job_run_logs scheduler_job_run_logs_job_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_job_run_logs
    ADD CONSTRAINT scheduler_job_run_logs_job_run_id_fkey FOREIGN KEY (job_run_id) REFERENCES public.scheduler_job_runs(job_run_id) ON DELETE CASCADE;


--
-- Name: scheduler_manual_run_requests scheduler_manual_run_requests_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_manual_run_requests
    ADD CONSTRAINT scheduler_manual_run_requests_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.scheduler_jobs(job_id) ON DELETE CASCADE;


--
-- Name: scheduler_manual_run_requests scheduler_manual_run_requests_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_manual_run_requests
    ADD CONSTRAINT scheduler_manual_run_requests_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(user_id) ON DELETE SET NULL;


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
-- Name: system_audit_log system_audit_log_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_audit_log
    ADD CONSTRAINT system_audit_log_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(user_id) ON DELETE SET NULL;


--
-- Name: trading_signals trading_signals_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_signals
    ADD CONSTRAINT trading_signals_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT;


--
-- Name: trading_signals trading_signals_universe_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_signals
    ADD CONSTRAINT trading_signals_universe_id_fkey FOREIGN KEY (universe_id) REFERENCES public.market_universes(universe_id) ON DELETE SET NULL;


--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(role_id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


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

\unrestrict TeI66ejnKfBnx9GixkEJl155dqsShvwz2ld0UAqUaL6CNMhLhsWGXtNIpZVkGhb

