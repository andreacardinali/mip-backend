BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS display_timezone text NOT NULL DEFAULT 'UTC';

CREATE OR REPLACE FUNCTION public.sp_get_user_display_timezone(p_user_id uuid)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT display_timezone FROM public.users WHERE user_id = p_user_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_update_user_preferences(
    p_user_id uuid, p_reporting_currency character, p_display_timezone text
) RETURNS jsonb LANGUAGE sql AS $$
    WITH updated AS (
        UPDATE public.users
        SET reporting_currency = p_reporting_currency,
            display_timezone = p_display_timezone, updated_at = now()
        WHERE user_id = p_user_id
        RETURNING user_id, username, email, is_active, reporting_currency, display_timezone
    )
    SELECT to_jsonb(updated) FROM updated;
$$;

COMMIT;