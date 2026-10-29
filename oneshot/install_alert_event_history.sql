BEGIN;

CREATE OR REPLACE FUNCTION public.sp_alert_mark_notification_skipped(p_event_id uuid, p_status text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_status IS NULL OR p_status NOT IN ('DISABLED', 'PREFERENCE_DISABLED', 'SOURCE_NOT_FOUND', 'DUPLICATE') THEN
        RAISE EXCEPTION 'Invalid notification skip reason';
    END IF;
    UPDATE public.alert_events
    SET payload = jsonb_set(payload, '{notification_skip_reason}', to_jsonb(p_status))
    WHERE alert_event_id = p_event_id AND notification_id IS NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_alert_list_user_events(p_user_id uuid, p_rule_id uuid, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0) RETURNS SETOF jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'alert_event_id', e.alert_event_id,
        'rule_id', e.rule_id,
        'instrument_id', e.instrument_id,
        'signal_id', e.signal_id,
        'payload', e.payload,
        'created_at', e.created_at,
        'notification_id', e.notification_id,
        'notification_status', n.status,
        'notification_skip_reason', e.payload ->> 'notification_skip_reason',
        'sent_at', n.sent_at
    )
    FROM public.alert_events e
    JOIN public.alert_rules r ON r.rule_id = e.rule_id AND r.user_id = p_user_id
    LEFT JOIN public.notifications n ON n.notification_id = e.notification_id
        AND n.user_id = p_user_id
    WHERE e.user_id = p_user_id AND e.rule_id = p_rule_id
    ORDER BY e.created_at DESC, e.alert_event_id DESC
    LIMIT least(greatest(coalesce(p_limit, 25), 1), 100)
    OFFSET greatest(coalesce(p_offset, 0), 0)
$$;

COMMIT;