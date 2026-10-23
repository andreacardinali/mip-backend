--
-- PostgreSQL database dump
--

\restrict 29TkS9NbOviGypvCYim4sbKwnPV1neLjXYM1mGh9PMsJDRf859cs0bkKpNcrzBY

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
    ON CONFLICT (alias_symbol) DO NOTHING;

    IF v_source.symbol IS DISTINCT FROM v_target.symbol THEN
        INSERT INTO instrument_aliases (
            instrument_id,
            alias_symbol
        )
        VALUES (
            p_target_instrument_id,
            v_source.symbol
        )
        ON CONFLICT (alias_symbol) DO NOTHING;
    END IF;

    DELETE FROM instrument_aliases
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
                    market_price_refresh_queue.status =
                    'PROCESSING'
                THEN
                    market_price_refresh_queue.status
                ELSE
                    'PENDING'
            END,

        attempt_count =
            CASE
                WHEN
                    market_price_refresh_queue.status =
                    'PROCESSING'
                THEN
                    market_price_refresh_queue.attempt_count
                ELSE
                    0
            END,

        next_attempt_at =
            CASE
                WHEN
                    market_price_refresh_queue.status =
                    'PROCESSING'
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

        last_error = NULL,
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
                p.quantity * i.last_price,
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
        COALESCE(SUM(
            CASE
                WHEN pos.position_id IS NULL OR pos.avg_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * pos.avg_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS invested_value_native,
        COALESCE(SUM(
            CASE
                WHEN pos.position_id IS NULL OR i.last_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * i.last_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS portfolio_value_native
    FROM portfolios pf
    JOIN users u
      ON u.user_id = pf.user_id
    LEFT JOIN positions pos
      ON pos.portfolio_id = pf.portfolio_id
    LEFT JOIN instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE pf.portfolio_id = p_portfolio_id
      AND pf.user_id = p_user_id
    GROUP BY pf.base_currency, u.reporting_currency
), totals AS (
    SELECT
        base_currency,
        reporting_currency,
        invested_value_native,
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
    fx_convert(invested_value_native, base_currency, reporting_currency),
    fx_convert(portfolio_value_native, base_currency, reporting_currency),
    fx_convert(profit_loss_native, base_currency, reporting_currency),
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
        COALESCE(SUM(
            CASE
                WHEN pos.avg_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * pos.avg_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS invested_value,
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
    v_scope := UPPER(COALESCE(NULLIF(BTRIM(p_scope), ''), 'MY_ASSETS'));
    v_query := NULLIF(BTRIM(p_query), '');

    IF v_scope NOT IN ('MY_ASSETS', 'PORTFOLIO', 'WATCHLIST', 'ALL') THEN
        RAISE EXCEPTION 'Unsupported catalyst scope: %', p_scope;
    END IF;

    IF p_limit < 1 OR p_limit > 2000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 2000';
    END IF;

    RETURN QUERY
    WITH user_entities AS (
        SELECT
            scoped_entities.entity_id,
            BOOL_OR(scoped_entities.in_portfolio) AS in_portfolio,
            BOOL_OR(scoped_entities.in_watchlist) AS in_watchlist
        FROM (
            SELECT
                i.entity_id,
                true AS in_portfolio,
                false AS in_watchlist
            FROM public.positions pos
            JOIN public.portfolios pf
              ON pf.portfolio_id = pos.portfolio_id
            JOIN public.instruments i
              ON i.instrument_id = pos.instrument_id
            WHERE pf.user_id = p_user_id

            UNION ALL

            SELECT
                wi.entity_id,
                false AS in_portfolio,
                true AS in_watchlist
            FROM public.watchlist_items wi
            JOIN public.watchlists w
              ON w.watchlist_id = wi.watchlist_id
            WHERE w.user_id = p_user_id
        ) scoped_entities
        GROUP BY scoped_entities.entity_id
    ), preferred_instrument AS (
        SELECT DISTINCT ON (i.entity_id)
            i.entity_id,
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
    )
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
        c.raw_payload,
        COALESCE(ue.in_portfolio, false),
        COALESCE(ue.in_watchlist, false),
        CASE
            WHEN COALESCE(ue.in_portfolio, false)
             AND COALESCE(ue.in_watchlist, false) THEN 'BOTH'
            WHEN COALESCE(ue.in_portfolio, false) THEN 'PORTFOLIO'
            WHEN COALESCE(ue.in_watchlist, false) THEN 'WATCHLIST'
            ELSE 'GLOBAL'
        END::varchar AS asset_scope
    FROM public.catalysts c
    JOIN public.entities e
      ON e.entity_id = c.entity_id
    LEFT JOIN user_entities ue
      ON ue.entity_id = c.entity_id
    LEFT JOIN preferred_instrument pi
      ON pi.entity_id = c.entity_id
    WHERE (p_date_from IS NULL OR c.catalyst_date >= p_date_from)
      AND (p_date_to IS NULL OR c.catalyst_date <= p_date_to)
      AND (
          NULLIF(BTRIM(p_catalyst_type), '') IS NULL
          OR UPPER(c.catalyst_type) = UPPER(BTRIM(p_catalyst_type))
      )
      AND (
          NULLIF(BTRIM(p_status), '') IS NULL
          OR UPPER(c.status) = UPPER(BTRIM(p_status))
      )
      AND (p_importance_min IS NULL OR c.importance >= p_importance_min)
      AND (
          v_query IS NULL
          OR e.name ILIKE '%' || v_query || '%'
          OR pi.symbol ILIKE '%' || v_query || '%'
          OR c.title ILIKE '%' || v_query || '%'
          OR c.source ILIKE '%' || v_query || '%'
      )
      AND CASE v_scope
          WHEN 'MY_ASSETS' THEN ue.entity_id IS NOT NULL
          WHEN 'PORTFOLIO' THEN COALESCE(ue.in_portfolio, false)
          WHEN 'WATCHLIST' THEN COALESCE(ue.in_watchlist, false)
          ELSE true
      END
    ORDER BY
        c.catalyst_date ASC NULLS LAST,
        c.importance DESC NULLS LAST,
        e.name,
        c.catalyst_id
    LIMIT p_limit;
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
BEGIN
    v_symbol := upper(nullif(btrim(p_symbol), ''));
    v_exchange := upper(nullif(btrim(p_exchange), ''));
    v_system_code := upper(nullif(btrim(p_system_code), ''));

    -- A canonical MIC already known by MIP wins.
    IF v_exchange IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM public.exchanges e
           WHERE upper(e.exchange) = v_exchange
       )
    THEN
        RETURN v_exchange;
    END IF;

    -- Translate provider-specific exchange codes through the existing mapping table.
    IF v_exchange IS NOT NULL AND v_system_code IS NOT NULL THEN
        SELECT esm.exchange
        INTO v_mapped_exchange
        FROM public.exchange_source_mappings esm
        JOIN public.external_systems es
          ON es.system_id = esm.system_id
        WHERE upper(es.code) = v_system_code
          AND (
              upper(esm.source_exchange_code) = v_exchange
              OR upper(coalesce(esm.source_exchange_name, '')) = v_exchange
          )
        ORDER BY esm.observed_at DESC
        LIMIT 1;

        IF v_mapped_exchange IS NOT NULL THEN
            RETURN upper(v_mapped_exchange);
        END IF;
    END IF;

    -- Conservative suffix-to-MIC fallback, used only when the provider did not
    -- supply a canonical exchange. Symbols are preserved unchanged.
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

    -- Keep an explicit provider value when no safe mapping is available.
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
    updated_at timestamp with time zone DEFAULT now() NOT NULL
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
    price_as_of timestamp with time zone
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
-- Name: market_price_refresh_queue market_price_refresh_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT market_price_refresh_queue_pkey PRIMARY KEY (job_id);


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
-- Name: identities uq_identities_type_value; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT uq_identities_type_value UNIQUE (identity_type_id, normalized_value);


--
-- Name: market_price_refresh_queue uq_market_price_refresh_instrument; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT uq_market_price_refresh_instrument UNIQUE (instrument_id);


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
-- Name: ix_market_price_refresh_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_market_price_refresh_due ON public.market_price_refresh_queue USING btree (status, next_attempt_at) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RETRY'::character varying])::text[]));


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
-- Name: ix_sync_mapping_external_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sync_mapping_external_lookup ON public.sync_mapping USING btree (system_id, internal_type, external_id) WHERE ((system_id IS NOT NULL) AND (external_id IS NOT NULL));


--
-- Name: ix_sync_mapping_internal_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sync_mapping_internal_lookup ON public.sync_mapping USING btree (internal_type, internal_id) WHERE (internal_id IS NOT NULL);


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
-- Name: entities trg_entities_validate_canonical_name; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trg_entities_validate_canonical_name AFTER INSERT OR UPDATE OF canonical_name_observation_id ON public.entities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_validate_entity_canonical_name();


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
-- Name: market_price_refresh_queue market_price_refresh_queue_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_price_refresh_queue
    ADD CONSTRAINT market_price_refresh_queue_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(instrument_id) ON DELETE CASCADE;


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

\unrestrict 29TkS9NbOviGypvCYim4sbKwnPV1neLjXYM1mGh9PMsJDRf859cs0bkKpNcrzBY

