BEGIN;

CREATE OR REPLACE FUNCTION public.sp_register_exchange_alias(p_exchange character varying, p_system_code character varying, p_alias character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_alias varchar;
    v_system_id uuid;
    v_system_count integer;
BEGIN
    v_alias := upper(nullif(btrim(p_alias), ''));
    IF v_alias IS NULL OR length(v_alias) > 100 THEN
        RAISE EXCEPTION 'Exchange alias must contain between 1 and 100 characters';
    END IF;
    PERFORM 1 FROM public.exchanges WHERE exchange = p_exchange FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Existing exchange row required: %', p_exchange;
    END IF;
    SELECT count(*) INTO v_system_count FROM public.external_systems
    WHERE upper(btrim(code)) = upper(btrim(p_system_code)) AND active;
    IF v_system_count <> 1 THEN
        RAISE EXCEPTION 'Expected one active external system for %', p_system_code;
    END IF;
    SELECT system_id INTO v_system_id FROM public.external_systems
    WHERE upper(btrim(code)) = upper(btrim(p_system_code)) AND active;

    LOCK TABLE public.exchange_source_mappings IN SHARE ROW EXCLUSIVE MODE;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE system_id = v_system_id AND exchange <> p_exchange
          AND (upper(btrim(source_exchange_code)) = v_alias
               OR upper(btrim(source_exchange_name)) = v_alias)
    ) THEN
        RAISE EXCEPTION 'Exchange alias % already belongs to another row for provider %',
            v_alias, p_system_code;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.exchange_source_mappings
        WHERE system_id = v_system_id AND exchange = p_exchange
          AND (upper(btrim(source_exchange_code)) = v_alias
               OR upper(btrim(source_exchange_name)) = v_alias)
    ) THEN
        RETURN;
    END IF;
    INSERT INTO public.exchange_source_mappings
        (exchange, system_id, source_exchange_code, raw_payload)
    VALUES (p_exchange, v_system_id, v_alias,
            jsonb_build_object('registration', 'manual'));
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_normalize_listing_exchange(p_symbol character varying, p_exchange character varying, p_system_code character varying DEFAULT NULL::character varying) RETURNS character varying
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_symbol varchar;
    v_exchange varchar;
    v_system_code varchar;
    v_mapped_exchange varchar;
    v_mapped_exchanges varchar[];
BEGIN
    v_symbol := upper(nullif(btrim(p_symbol), ''));
    v_exchange := upper(nullif(btrim(p_exchange), ''));
    v_system_code := upper(nullif(btrim(p_system_code), ''));

    IF v_exchange IS NOT NULL AND v_system_code IS NOT NULL THEN
        SELECT array_agg(DISTINCT esm.exchange)
        INTO v_mapped_exchanges
        FROM public.exchange_source_mappings esm
        JOIN public.external_systems es
          ON es.system_id = esm.system_id
        WHERE upper(btrim(es.code)) = v_system_code
          AND es.active
          AND (
              upper(btrim(esm.source_exchange_code)) = v_exchange
              OR upper(btrim(esm.source_exchange_name)) = v_exchange
          );

        IF cardinality(v_mapped_exchanges) > 1 THEN
            RAISE EXCEPTION 'Ambiguous exchange alias % for provider %',
                v_exchange, v_system_code;
        END IF;
        IF cardinality(v_mapped_exchanges) = 1 THEN
            RETURN v_mapped_exchanges[1];
        END IF;
    END IF;

    SELECT e.exchange INTO v_mapped_exchange
    FROM public.exchanges e
    WHERE upper(btrim(e.exchange)) = v_exchange;
    IF FOUND THEN
        RETURN v_mapped_exchange;
    END IF;

    IF v_symbol IS NOT NULL THEN
        v_mapped_exchange := CASE
            WHEN v_symbol LIKE '%.MI' THEN 'XMIL'
            WHEN v_symbol LIKE '%.DE' THEN 'XETR'
            WHEN v_symbol LIKE '%.SW' THEN 'XSWX'
            WHEN v_symbol LIKE '%.L'  THEN 'XLON'
            WHEN v_symbol LIKE '%.PA' THEN 'XPAR'
            WHEN v_symbol LIKE '%.AS' THEN 'XAMS'
            WHEN v_symbol LIKE '%.BR' THEN 'XBRU'
            WHEN v_symbol LIKE '%.MC' THEN 'XMAD'
            WHEN v_symbol LIKE '%.LS' THEN 'XLIS'
            WHEN v_symbol LIKE '%.VI' THEN 'XWBO'
            WHEN v_symbol LIKE '%.ST' THEN 'XSTO'
            WHEN v_symbol LIKE '%.CO' THEN 'XCSE'
            WHEN v_symbol LIKE '%.OL' THEN 'XOSL'
            WHEN v_symbol LIKE '%.HE' THEN 'XHEL'
            WHEN v_symbol LIKE '%.TO' THEN 'XTSE'
            WHEN v_symbol LIKE '%.V'  THEN 'XTSX'
            WHEN v_symbol LIKE '%.AX' THEN 'XASX'
            WHEN v_symbol LIKE '%.HK' THEN 'XHKG'
            WHEN v_symbol LIKE '%.T'  THEN 'XTKS'
            ELSE NULL
        END;

        IF v_mapped_exchange IS NOT NULL THEN
            RETURN v_mapped_exchange;
        END IF;
    END IF;

    RETURN v_exchange;
END;
$$;

COMMIT;