BEGIN;

SET LOCAL TIME ZONE 'UTC';

CREATE OR REPLACE FUNCTION public.sp_sync_exchange_catalog(p_catalog jsonb)
RETURNS integer
LANGUAGE plpgsql
AS $function$
DECLARE
    v_system_id uuid;
    v_item jsonb;
    v_code text;
    v_legacy text;
BEGIN
    IF jsonb_typeof(p_catalog) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Exchange catalog must be an array';
    END IF;
    IF jsonb_array_length(p_catalog) = 0 THEN
        RAISE EXCEPTION 'Exchange catalog must not be empty';
    END IF;

    SELECT system_id INTO STRICT v_system_id
    FROM public.external_systems
    WHERE lower(btrim(code)) = 'eulerpool' AND active;

    LOCK TABLE public.exchanges IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.exchange_source_mappings IN SHARE ROW EXCLUSIVE MODE;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_catalog) AS rows(item)
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR jsonb_typeof(item->'code') IS DISTINCT FROM 'string'
           OR COALESCE(item->>'code', '') !~ '^[A-Z0-9]{4,20}$'
           OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
           OR NULLIF(btrim(item->>'name'), '') IS NULL
           OR length(item->>'name') > 255
           OR length(item->>'country') > 10
           OR (
                item ? 'calendar_name'
                AND jsonb_typeof(item->'calendar_name') NOT IN ('string', 'null')
           )
           OR length(item->>'calendar_name') > 64
           OR (
                NULLIF(item->>'timezone', '') IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM pg_timezone_names
                    WHERE name = item->>'timezone'
                )
           )
    ) THEN
        RAISE EXCEPTION 'Invalid Eulerpool exchange catalog entry';
    END IF;

    IF EXISTS (
        SELECT item->>'code'
        FROM jsonb_array_elements(p_catalog) AS rows(item)
        GROUP BY item->>'code'
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate Eulerpool exchange catalog codes';
    END IF;

    SELECT string_agg(e.exchange, ', ' ORDER BY e.exchange)
    INTO v_legacy
    FROM public.exchanges e
    WHERE NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_catalog) AS rows(item)
        WHERE item->>'code' = e.exchange
    );
    IF v_legacy IS NOT NULL THEN
        RAISE EXCEPTION
            'Noncanonical existing exchanges: %. Reconcile explicitly before catalog synchronization',
            v_legacy;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.exchange_source_mappings m
        JOIN jsonb_array_elements(p_catalog) AS rows(item)
          ON upper(btrim(m.source_exchange_code)) = item->>'code'
          OR upper(btrim(m.source_exchange_name)) = upper(btrim(item->>'name'))
        WHERE m.system_id = v_system_id
          AND m.exchange <> item->>'code'
    ) THEN
        RAISE EXCEPTION 'Eulerpool catalog conflicts with existing exchange mappings';
    END IF;

    FOR v_item IN
        SELECT item FROM jsonb_array_elements(p_catalog) AS rows(item)
    LOOP
        v_code := v_item->>'code';

        INSERT INTO public.exchanges (
            exchange,
            exchangefullname,
            country_code,
            timezone_name,
            calendar_name
        )
        VALUES (
            v_code,
            btrim(v_item->>'name'),
            NULLIF(v_item->>'country', ''),
            NULLIF(v_item->>'timezone', ''),
            NULLIF(v_item->>'calendar_name', '')
        )
        ON CONFLICT (exchange) DO UPDATE SET
            exchangefullname = EXCLUDED.exchangefullname,
            country_code = EXCLUDED.country_code,
            timezone_name = COALESCE(EXCLUDED.timezone_name, exchanges.timezone_name),
            calendar_name = COALESCE(EXCLUDED.calendar_name, exchanges.calendar_name),
            updated_at = now();

        INSERT INTO public.exchange_source_mappings (
            exchange,
            system_id,
            source_exchange_code,
            source_exchange_name,
            raw_payload
        )
        VALUES (
            v_code,
            v_system_id,
            v_code,
            btrim(v_item->>'name'),
            jsonb_build_object(
                'authority', 'eulerpool_catalog',
                'catalog', v_item->'raw_payload'
            )
        )
        ON CONFLICT (exchange, system_id, source_exchange_code) DO UPDATE SET
            source_exchange_name = EXCLUDED.source_exchange_name,
            raw_payload = EXCLUDED.raw_payload,
            observed_at = now();
    END LOOP;

    RETURN jsonb_array_length(p_catalog);
END;
$function$;

COMMIT;