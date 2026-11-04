BEGIN;

CREATE TABLE IF NOT EXISTS public.user_notification_delivery (
    user_id uuid PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    paused boolean NOT NULL DEFAULT false,
    default_source_id uuid REFERENCES public.user_sources(source_id),
    daily_portfolio_report_time time NOT NULL DEFAULT TIME '21:00',
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.user_notification_delivery
    ADD COLUMN IF NOT EXISTS daily_portfolio_report_time time NOT NULL DEFAULT TIME '21:00';

INSERT INTO public.user_notification_delivery (user_id, default_source_id)
SELECT u.user_id, destination.source_id
FROM public.users u
LEFT JOIN LATERAL (
    SELECT us.source_id
    FROM public.user_sources us
    WHERE us.user_id = u.user_id
      AND COALESCE((us.config->>'enabled')::boolean, true)
    ORDER BY us.created_at DESC, us.source_id
    LIMIT 1
) destination ON true
ON CONFLICT (user_id) DO NOTHING;

UPDATE public.alert_rules ar
SET conditions = ar.conditions || jsonb_build_object('source_id', ns.source_id),
    updated_at = now()
FROM public.notification_subscriptions ns
JOIN public.user_notification_delivery delivery ON delivery.user_id = ns.user_id
JOIN public.user_sources us ON us.source_id = ns.source_id AND us.user_id = ns.user_id
WHERE ar.user_id = ns.user_id
  AND ar.alert_type = UPPER(ns.event_type)
  AND ns.portfolio_id IS NULL
  AND ns.source_id IS DISTINCT FROM delivery.default_source_id
  AND COALESCE((us.config->>'enabled')::boolean, true)
  AND ar.conditions->>'source_id' IS NULL;

DROP FUNCTION IF EXISTS public.sp_resolve_user_notification_target(
    uuid, varchar, varchar, varchar
);
DROP FUNCTION IF EXISTS public.sp_resolve_user_notification_target(
    uuid, varchar, varchar, varchar, uuid
);
DROP FUNCTION IF EXISTS public.sp_get_user_notification_delivery(uuid);

CREATE FUNCTION public.sp_get_user_notification_delivery(p_user_id uuid)
RETURNS TABLE(
    paused boolean,
    default_source_id uuid,
    daily_portfolio_report_time time
)
LANGUAGE sql STABLE
AS $$
    SELECT COALESCE(delivery.paused, false), delivery.default_source_id,
           COALESCE(delivery.daily_portfolio_report_time, TIME '21:00')
    FROM public.users u
    LEFT JOIN public.user_notification_delivery delivery ON delivery.user_id = u.user_id
    WHERE u.user_id = p_user_id;
$$;

DROP FUNCTION IF EXISTS public.sp_save_user_notification_delivery(uuid, boolean, uuid);

CREATE OR REPLACE FUNCTION public.sp_save_user_notification_delivery(
    p_user_id uuid, p_paused boolean, p_source_id uuid, p_report_time time
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_paused IS NULL THEN
        RAISE EXCEPTION 'Notification pause must be a boolean' USING ERRCODE = '22023';
    END IF;

    IF p_report_time IS NULL THEN
        RAISE EXCEPTION 'Daily portfolio report time is required' USING ERRCODE = '22023';
    END IF;

    IF p_source_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.user_sources us
        WHERE us.user_id = p_user_id AND us.source_id = p_source_id
          AND COALESCE((us.config->>'enabled')::boolean, true)
    ) THEN
        RAISE EXCEPTION 'Enabled notification source does not belong to user'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.user_notification_delivery (
        user_id, paused, default_source_id, daily_portfolio_report_time
    )
    VALUES (p_user_id, p_paused, p_source_id, p_report_time)
    ON CONFLICT (user_id) DO UPDATE
    SET paused = EXCLUDED.paused,
        default_source_id = EXCLUDED.default_source_id,
        daily_portfolio_report_time = EXCLUDED.daily_portfolio_report_time,
        updated_at = now();
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_save_user_screener_summary_subscription(
    p_user_id uuid, p_enabled boolean
)
RETURNS boolean
LANGUAGE sql
AS $$
    INSERT INTO public.notification_subscriptions (
        user_id, portfolio_id, event_type, source_id, enabled, severity_min, config
    )
    VALUES (p_user_id, NULL, 'SCREENER_SUMMARY', NULL, p_enabled, NULL, '{}'::jsonb)
    ON CONFLICT (user_id, event_type) WHERE portfolio_id IS NULL
    DO UPDATE SET enabled = EXCLUDED.enabled, source_id = NULL, severity_min = NULL
    RETURNING enabled;
$$;

CREATE OR REPLACE FUNCTION public.sp_resolve_user_notification_target(
    p_user_id uuid,
    p_notification_type varchar,
    p_severity varchar,
    p_fallback_channel varchar,
    p_source_id uuid
)
RETURNS TABLE(source_id uuid, channel varchar, resolution_status varchar)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_delivery record;
    v_source public.user_sources%ROWTYPE;
BEGIN
    SELECT * INTO v_delivery FROM public.sp_get_user_notification_delivery(p_user_id);
    IF v_delivery.paused THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PAUSED'::varchar;
        RETURN;
    END IF;

    IF UPPER(p_notification_type) = 'SCREENER_SUMMARY' AND NOT EXISTS (
        SELECT 1 FROM public.notification_subscriptions ns
        WHERE ns.user_id = p_user_id AND ns.portfolio_id IS NULL
          AND UPPER(ns.event_type) = 'SCREENER_SUMMARY' AND ns.enabled
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
        RETURN;
    END IF;

    SELECT us.* INTO v_source
    FROM public.user_sources us
    WHERE us.user_id = p_user_id
      AND us.source_id = COALESCE(p_source_id, v_delivery.default_source_id)
      AND COALESCE((us.config->>'enabled')::boolean, true);

    IF v_source.source_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'SOURCE_NOT_FOUND'::varchar;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_source.source_id, UPPER(v_source.source_type)::varchar, 'RESOLVED'::varchar;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_resolve_user_notification_target(
    p_user_id uuid,
    p_notification_type varchar,
    p_severity varchar,
    p_fallback_channel varchar DEFAULT 'TELEGRAM'::varchar
)
RETURNS TABLE(source_id uuid, channel varchar, resolution_status varchar)
LANGUAGE sql STABLE
AS $$
    SELECT * FROM public.sp_resolve_user_notification_target(
        p_user_id, p_notification_type, p_severity, p_fallback_channel, NULL::uuid
    );
$$;

CREATE OR REPLACE FUNCTION public.sp_delete_user_notification_source(p_user_id uuid, p_source_id uuid)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.user_notification_delivery delivery
        WHERE delivery.user_id = p_user_id AND delivery.default_source_id = p_source_id
    ) OR EXISTS (
        SELECT 1 FROM public.alert_rules ar
        WHERE ar.user_id = p_user_id AND ar.conditions->>'source_id' = p_source_id::text
    ) THEN
        RAISE EXCEPTION 'Notification source is used by the default or a rule';
    END IF;

        UPDATE public.notification_subscriptions ns
        SET source_id = NULL
        WHERE ns.user_id = p_user_id AND ns.source_id = p_source_id
            AND UPPER(ns.event_type) = 'SCREENER_SUMMARY';

    DELETE FROM public.user_sources us
    WHERE us.user_id = p_user_id AND us.source_id = p_source_id;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_claim_notifications(p_limit integer)
RETURNS TABLE(
    notification_id uuid, user_id uuid, source_id uuid, channel varchar,
    notification_key varchar, notification_type varchar, severity varchar,
    title text, message text, context jsonb, source_external_id text,
    source_config jsonb, attempt_count integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT n.notification_id
        FROM public.notifications n
        JOIN public.user_sources us ON us.source_id = n.source_id AND us.user_id = n.user_id
        LEFT JOIN public.user_notification_delivery delivery ON delivery.user_id = n.user_id
        WHERE n.status IN ('PENDING', 'RETRY')
          AND n.source_id IS NOT NULL
          AND NOT COALESCE(delivery.paused, false)
          AND COALESCE((us.config->>'enabled')::boolean, true)
          AND COALESCE(n.next_attempt_at, now()) <= now()
        ORDER BY n.created_at
        FOR UPDATE OF n SKIP LOCKED
        LIMIT GREATEST(p_limit, 1)
    ), claimed AS (
        UPDATE public.notifications n
        SET status = 'PROCESSING', processing_started_at = now(),
            attempt_count = n.attempt_count + 1, last_error = NULL
        FROM candidates candidate
        WHERE n.notification_id = candidate.notification_id
        RETURNING n.*
    )
    SELECT claimed.notification_id, claimed.user_id, claimed.source_id, claimed.channel,
        claimed.notification_key, claimed.notification_type, claimed.severity,
        claimed.title, claimed.message, claimed.context, us.external_id, us.config,
        claimed.attempt_count
    FROM claimed
    JOIN public.user_sources us ON us.source_id = claimed.source_id AND us.user_id = claimed.user_id;
END;
$$;

COMMIT;