BEGIN;

CREATE OR REPLACE FUNCTION public.sp_alert_list_user_rules(p_user_id uuid)
RETURNS TABLE(
    rule_id uuid,
    name character varying,
    alert_type character varying,
    severity character varying,
    channel character varying,
    conditions jsonb,
    cooldown_minutes integer,
    enabled boolean
)
LANGUAGE sql STABLE
AS $$
    SELECT r.rule_id, r.name, r.alert_type, r.severity, r.channel,
           r.conditions, r.cooldown_minutes, r.enabled
    FROM public.alert_rules r
    WHERE r.user_id = p_user_id
    ORDER BY r.name, r.rule_id
$$;

CREATE OR REPLACE FUNCTION public.sp_alert_delete_rule(p_rule_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM public.alert_rules
    WHERE rule_id = p_rule_id AND user_id = p_user_id;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_alert_set_rule_enabled(p_rule_id uuid, p_user_id uuid, p_enabled boolean) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_enabled IS NULL THEN
        RAISE EXCEPTION 'Enabled state is required';
    END IF;
    UPDATE public.alert_rules
    SET enabled = p_enabled
    WHERE rule_id = p_rule_id AND user_id = p_user_id;
    RETURN FOUND;
END;
$$;

COMMIT;