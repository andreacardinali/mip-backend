BEGIN;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS source_id uuid,
    ADD COLUMN IF NOT EXISTS channel varchar(50),
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS processing_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_error text,
    ADD COLUMN IF NOT EXISTS delivered_message_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notifications_source_id_fkey'
          AND conrelid = 'notifications'::regclass
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT notifications_source_id_fkey
            FOREIGN KEY (source_id)
            REFERENCES user_sources(source_id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS ix_notifications_delivery_queue
ON notifications (status, next_attempt_at, created_at)
WHERE status IN ('PENDING', 'RETRY');

CREATE INDEX IF NOT EXISTS ix_notifications_source_id
ON notifications (source_id);

-- Existing undelivered rows are not dispatched automatically because they did
-- not capture a source_id at enqueue time. New records always store source_id.

CREATE OR REPLACE FUNCTION sp_enqueue_notification
(
    p_user_id uuid,
    p_source_id uuid,
    p_channel varchar,
    p_notification_key varchar,
    p_notification_type varchar,
    p_entity_id uuid,
    p_symbol varchar,
    p_event_key varchar,
    p_severity varchar,
    p_title text,
    p_message text,
    p_context jsonb
)
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_notification_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM user_sources us
        WHERE us.source_id = p_source_id
          AND us.user_id = p_user_id
          AND UPPER(us.source_type) = UPPER(p_channel)
          AND us.enabled = true
    ) THEN
        RAISE EXCEPTION
            'Enabled source % for channel % does not belong to user %',
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
        COALESCE(p_context, '{}'::jsonb),
        'PENDING',
        now(),
        0,
        now()
    )
    ON CONFLICT (user_id, notification_key)
    DO NOTHING
    RETURNING notification_id
    INTO v_notification_id;

    RETURN v_notification_id;
END;
$$;

CREATE OR REPLACE FUNCTION sp_claim_notifications
(
    p_limit integer
)
RETURNS TABLE
(
    notification_id uuid,
    user_id uuid,
    source_id uuid,
    channel varchar,
    notification_key varchar,
    notification_type varchar,
    severity varchar,
    title text,
    message text,
    context jsonb,
    source_external_id varchar,
    source_config jsonb,
    attempt_count integer
)
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
         AND us.enabled = true
        WHERE n.status IN ('PENDING', 'RETRY')
          AND n.source_id IS NOT NULL
          AND COALESCE(n.next_attempt_at, now()) <= now()
        ORDER BY n.created_at
        FOR UPDATE OF n SKIP LOCKED
        LIMIT GREATEST(p_limit, 1)
    ),
    claimed AS
    (
        UPDATE notifications n
        SET
            status = 'PROCESSING',
            processing_started_at = now(),
            attempt_count = n.attempt_count + 1,
            last_error = NULL
        FROM candidates c
        WHERE n.notification_id = c.notification_id
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

CREATE OR REPLACE FUNCTION sp_mark_notification_sent
(
    p_notification_id uuid,
    p_provider_message_id text
)
RETURNS void
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

CREATE OR REPLACE FUNCTION sp_mark_notification_failed
(
    p_notification_id uuid,
    p_error text,
    p_max_retries integer,
    p_retry_base_seconds integer
)
RETURNS varchar
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

CREATE OR REPLACE FUNCTION sp_release_stale_notifications
(
    p_timeout_seconds integer
)
RETURNS integer
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

COMMIT;
