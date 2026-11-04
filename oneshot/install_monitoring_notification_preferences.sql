BEGIN;

CREATE OR REPLACE FUNCTION public.sp_get_user_notification_preferences(p_user_id uuid) RETURNS TABLE(notification_type character varying, label character varying, description text, enabled boolean, source_id uuid, severity_min character varying)
    LANGUAGE sql STABLE
    AS $$
WITH notification_types(notification_type, label, description, default_enabled) AS (
    VALUES
        ('UPCOMING_CATALYST'::varchar, 'Catalyst imminenti'::varchar,
         'Eventi attesi nei prossimi giorni.'::text, true),
        ('BREAKING_CATALYST'::varchar, 'Catalyst urgenti'::varchar,
         'Notizie ed eventi rilevanti appena rilevati.'::text, true),
        ('DAILY_MOVER'::varchar, 'Movimenti giornalieri'::varchar,
         'Variazioni di prezzo rilevanti rilevate durante la giornata.'::text, false),
        ('MARKET_SIGNAL'::varchar, 'Segnali di mercato'::varchar,
         'Segnali corrispondenti alle regole di monitoraggio.'::text, true),
        ('FIXED_PRICE'::varchar, 'Soglie di prezzo'::varchar,
         'Prezzi che soddisfano le soglie delle regole di monitoraggio.'::text, true),
        ('PERCENT_MOVE'::varchar, 'Variazioni percentuali'::varchar,
         'Variazioni rispetto alla chiusura storica di riferimento.'::text, true),
        ('SIGNAL_CHANGE'::varchar, 'Cambi del segnale'::varchar,
         'Eventi del ciclo di vita dei segnali monitorati.'::text, true),
        ('SCREENER_SUMMARY'::varchar, 'Riepilogo screener'::varchar,
         'Nuove opportunita, cambi del segnale e uscite dopo una scansione completa.'::text, false)
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
    WHEN 'DAILY_MOVER' THEN 3
    WHEN 'MARKET_SIGNAL' THEN 4
    WHEN 'FIXED_PRICE' THEN 5
    WHEN 'PERCENT_MOVE' THEN 6
    WHEN 'SIGNAL_CHANGE' THEN 7
    ELSE 8
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_upsert_user_notification_preference(p_user_id uuid, p_notification_type character varying, p_enabled boolean, p_source_id uuid, p_severity_min character varying DEFAULT NULL::character varying) RETURNS uuid
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
        'DAILY_MOVER',
        'MARKET_SIGNAL',
        'FIXED_PRICE',
        'PERCENT_MOVE',
        'SIGNAL_CHANGE',
        'SCREENER_SUMMARY'
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

CREATE OR REPLACE FUNCTION public.sp_resolve_user_notification_target(
    p_user_id uuid,
    p_notification_type character varying,
    p_severity character varying,
    p_fallback_channel character varying,
    p_source_id uuid
) RETURNS TABLE(source_id uuid, channel character varying, resolution_status character varying)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    RETURN QUERY
    SELECT * FROM public.sp_resolve_user_notification_target(
        p_user_id, p_notification_type, p_severity, p_fallback_channel,
        p_source_id, NULL::jsonb
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_resolve_user_notification_target(
    p_user_id uuid,
    p_notification_type character varying,
    p_severity character varying,
    p_fallback_channel character varying,
    p_source_id uuid,
    p_context jsonb
) RETURNS TABLE(source_id uuid, channel character varying, resolution_status character varying)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_delivery record;
    v_source public.user_sources%ROWTYPE;
    v_preference_enabled boolean;
    v_preference_source_id uuid;
    v_severity_min character varying;
    v_has_preference boolean;
    v_severity_rank integer;
    v_minimum_rank integer;
BEGIN
    SELECT * INTO v_delivery FROM public.sp_get_user_notification_delivery(p_user_id);
    IF v_delivery.paused THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PAUSED'::varchar;
        RETURN;
    END IF;

    SELECT p.enabled, p.source_id, p.severity_min
    INTO v_preference_enabled, v_preference_source_id, v_severity_min
    FROM public.sp_get_user_notification_preferences(p_user_id) p
    WHERE UPPER(p.notification_type) = UPPER(p_notification_type);
    v_has_preference := FOUND;

    IF NOT v_has_preference THEN
        SELECT ns.enabled, ns.source_id, ns.severity_min
        INTO v_preference_enabled, v_preference_source_id, v_severity_min
        FROM public.notification_subscriptions ns
        WHERE ns.user_id = p_user_id
          AND ns.portfolio_id IS NULL
          AND UPPER(ns.event_type) = UPPER(p_notification_type);
        v_has_preference := FOUND;
    END IF;

    IF v_has_preference AND NOT v_preference_enabled THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
        RETURN;
    END IF;

    IF v_has_preference AND v_severity_min IS NOT NULL THEN
        v_severity_rank := CASE UPPER(p_severity)
            WHEN 'LOW' THEN 1
            WHEN 'MEDIUM' THEN 2
            WHEN 'HIGH' THEN 3
            WHEN 'CRITICAL' THEN 4
            ELSE 0
        END;
        v_minimum_rank := CASE UPPER(v_severity_min)
            WHEN 'LOW' THEN 1
            WHEN 'MEDIUM' THEN 2
            WHEN 'HIGH' THEN 3
            WHEN 'CRITICAL' THEN 4
            ELSE 0
        END;
        IF v_severity_rank < v_minimum_rank THEN
            RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
            RETURN;
        END IF;
    END IF;

    IF COALESCE(p_context, '{}'::jsonb) ? 'alert_rule_id'
       AND NOT EXISTS (
           SELECT 1
           FROM public.alert_rules ar
           WHERE ar.rule_id::text = p_context->>'alert_rule_id'
             AND ar.user_id = p_user_id
             AND ar.enabled
             AND UPPER(ar.alert_type) = UPPER(p_notification_type)
       ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'PREFERENCE_DISABLED'::varchar;
        RETURN;
    END IF;

    SELECT us.* INTO v_source
    FROM public.user_sources us
    WHERE us.user_id = p_user_id
      AND us.source_id = COALESCE(
          p_source_id,
          v_preference_source_id,
          v_delivery.default_source_id
      )
      AND COALESCE((us.config->>'enabled')::boolean, true);

    IF v_source.source_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::varchar, 'SOURCE_NOT_FOUND'::varchar;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_source.source_id, UPPER(v_source.source_type)::varchar, 'RESOLVED'::varchar;
END;
$$;

COMMIT;