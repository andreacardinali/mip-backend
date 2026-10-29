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

COMMIT;