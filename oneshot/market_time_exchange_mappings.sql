SET LOCAL timezone = 'UTC';

LOCK TABLE public.exchanges IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    mapping record;
    existing record;
BEGIN
    FOR mapping IN
        SELECT * FROM (VALUES
            ('MIL', 'Milan', 'XMIL', 'Europe/Rome'),
            ('XMIL', 'XMIL', 'XMIL', 'Europe/Rome'),
            ('NASDAQ', 'NASDAQ', 'XNAS', 'America/New_York'),
            ('XNAS', 'NASDAQ', 'XNAS', 'America/New_York'),
            ('NYSE', 'New York Stock Exchange', 'XNYS', 'America/New_York'),
            ('XAMS', 'Euronext Amsterdam', 'XAMS', 'Europe/Amsterdam'),
            ('XETR', 'xetra', 'XETR', 'Europe/Berlin'),
            ('XLON', 'London', 'XLON', 'Europe/London')
        ) AS approved(exchange, full_name, calendar_name, timezone_name)
    LOOP
        SELECT e.exchangefullname, e.calendar_name, e.timezone_name
        INTO existing
        FROM public.exchanges e
        WHERE e.exchange = mapping.exchange;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Missing reviewed exchange: %', mapping.exchange;
        END IF;
        IF existing.exchangefullname IS DISTINCT FROM mapping.full_name THEN
            RAISE EXCEPTION 'Exchange identity changed for %: expected %, found %',
                mapping.exchange, mapping.full_name, existing.exchangefullname;
        END IF;
        IF (NULLIF(existing.calendar_name, '') IS NOT NULL
            AND existing.calendar_name IS DISTINCT FROM mapping.calendar_name)
            OR (NULLIF(existing.timezone_name, '') IS NOT NULL
                AND existing.timezone_name IS DISTINCT FROM mapping.timezone_name)
        THEN
            RAISE EXCEPTION 'Conflicting calendar/timezone for %; review explicitly',
                mapping.exchange;
        END IF;

        UPDATE public.exchanges
        SET calendar_name = mapping.calendar_name,
            timezone_name = mapping.timezone_name
        WHERE exchange = mapping.exchange
          AND (calendar_name IS DISTINCT FROM mapping.calendar_name
               OR timezone_name IS DISTINCT FROM mapping.timezone_name);
    END LOOP;
END;
$$;