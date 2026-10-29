SET LOCAL timezone = 'UTC';

DO $$
DECLARE
    expected record;
    actual_type text;
BEGIN
    FOR expected IN
        SELECT * FROM (VALUES
            ('filings', 'accepted_at', 'timestamp with time zone'),
            ('filings', 'filing_date', 'date'),
            ('catalysts', 'catalyst_date', 'timestamp with time zone'),
            ('catalysts', 'catalyst_day', 'date'),
            ('exchanges', 'timezone_name', 'character varying'),
            ('exchanges', 'market_open_time', 'time without time zone'),
            ('exchanges', 'market_close_time', 'time without time zone')
        ) AS columns(table_name, column_name, data_type)
    LOOP
        SELECT data_type INTO actual_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = expected.table_name
          AND column_name = expected.column_name;
        IF actual_type IS DISTINCT FROM expected.data_type THEN
            RAISE EXCEPTION 'Review temporal column %.%: expected %, found %. No automatic historical conversion is allowed.',
                expected.table_name, expected.column_name,
                expected.data_type, actual_type;
        END IF;
    END LOOP;
END;
$$;

ALTER TABLE public.exchanges
    ADD COLUMN IF NOT EXISTS calendar_name character varying(64);

DO $$
DECLARE
    legacy_oid oid := to_regprocedure(
        'public.sp_upsert_filing(text,text,text,date,timestamp without time zone,text,text,jsonb,text,text)'
    );
BEGIN
    IF legacy_oid IS NOT NULL AND EXISTS (
        SELECT 1 FROM pg_proc
        WHERE oid = legacy_oid
          AND (proacl IS NOT NULL OR pg_get_userbyid(proowner) <> current_user)
    ) THEN
        RAISE EXCEPTION 'Review and preserve filing function ownership/grants before replacing its signature';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS public.sp_upsert_filing(
    text, text, text, date, timestamp without time zone,
    text, text, jsonb, text, text
) RESTRICT;