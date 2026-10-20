BEGIN;

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS mic_code varchar(10),
    ADD COLUMN IF NOT EXISTS figi varchar(20),
    ADD COLUMN IF NOT EXISTS figi_composite varchar(20),
    ADD COLUMN IF NOT EXISTS figi_share_class varchar(20),
    ADD COLUMN IF NOT EXISTS market_sector varchar(50),
    ADD COLUMN IF NOT EXISTS metadata_source varchar(50),
    ADD COLUMN IF NOT EXISTS metadata_refreshed_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS uq_instruments_figi
    ON instruments(figi)
    WHERE figi IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_instruments_mic_code
    ON instruments(mic_code)
    WHERE mic_code IS NOT NULL;

CREATE OR REPLACE FUNCTION sp_apply_instrument_reference_metadata(
    p_instrument_id uuid,
    p_payload jsonb
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE instruments
    SET
        exchange = COALESCE(NULLIF(TRIM(p_payload->>'exchange'), ''), exchange),
        mic_code = COALESCE(NULLIF(UPPER(TRIM(p_payload->>'mic_code')), ''), mic_code),
        figi = COALESCE(NULLIF(UPPER(TRIM(p_payload->>'figi')), ''), figi),
        figi_composite = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'figi_composite')), ''),
            figi_composite
        ),
        figi_share_class = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'figi_share_class')), ''),
            figi_share_class
        ),
        market_sector = COALESCE(
            NULLIF(TRIM(p_payload->>'market_sector'), ''),
            market_sector
        ),
        metadata_source = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'metadata_source')), ''),
            metadata_source
        ),
        metadata_refreshed_at = CASE
            WHEN NULLIF(TRIM(p_payload->>'metadata_source'), '') IS NOT NULL
            THEN now()
            ELSE metadata_refreshed_at
        END
    WHERE instrument_id = p_instrument_id;
END;
$$;

-- Extend the two centralized persistence procedures without duplicating their
-- existing entity/profile logic: callers run the existing function and then this
-- metadata function with the returned/current instrument_id.

CREATE OR REPLACE FUNCTION sp_search_local_assets(
    p_query varchar,
    p_limit integer DEFAULT 20
) RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    v_result jsonb;
BEGIN
    SELECT COALESCE(jsonb_agg(row_data), '[]'::jsonb)
    INTO v_result
    FROM (
        SELECT jsonb_build_object(
            'symbol', i.symbol,
            'name', e.name,
            'exchange', i.exchange,
            'currency', i.currency_code,
            'type', i.instrument_type,
            'mic_code', i.mic_code,
            'figi', i.figi,
            'figi_composite', i.figi_composite,
            'figi_share_class', i.figi_share_class,
            'market_sector', i.market_sector,
            'metadata_source', i.metadata_source,
            'data_source', i.data_source
        ) AS row_data
        FROM instruments i
        JOIN entities e ON e.entity_id = i.entity_id
        WHERE LOWER(i.symbol) LIKE LOWER(p_query || '%')
           OR LOWER(e.name) LIKE LOWER(p_query || '%')
        ORDER BY
            CASE
                WHEN LOWER(i.symbol) = LOWER(p_query) THEN 1
                WHEN LOWER(i.symbol) LIKE LOWER(p_query || '%') THEN 2
                ELSE 3
            END,
            i.symbol
        LIMIT p_limit
    ) q;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION sp_get_entities_for_refresh(
    p_mode varchar DEFAULT 'MISSING',
    p_symbol varchar DEFAULT NULL
) RETURNS TABLE(entity_id uuid, instrument_id uuid, symbol varchar)
LANGUAGE plpgsql
AS $$
BEGIN
    IF UPPER(p_mode) NOT IN ('MISSING', 'ALL', 'SYMBOL') THEN
        RAISE EXCEPTION 'Invalid refresh mode: %', p_mode;
    END IF;
    IF UPPER(p_mode) = 'SYMBOL' AND NULLIF(TRIM(p_symbol), '') IS NULL THEN
        RAISE EXCEPTION 'p_symbol is required when p_mode is SYMBOL';
    END IF;

    RETURN QUERY
    SELECT e.entity_id, i.instrument_id, i.symbol
    FROM entities e
    JOIN instruments i ON i.entity_id = e.entity_id
    WHERE e.active = TRUE
      AND (
          (UPPER(p_mode) = 'MISSING' AND (
              UPPER(e.name) = UPPER(i.symbol)
              OR e.country_code IS NULL
              OR i.currency_code IS NULL
              OR i.data_source IS NULL
              OR i.exchange IS NULL
              OR i.figi IS NULL
              OR i.mic_code IS NULL
          ))
          OR UPPER(p_mode) = 'ALL'
          OR (UPPER(p_mode) = 'SYMBOL' AND UPPER(i.symbol) = UPPER(TRIM(p_symbol)))
      )
    ORDER BY i.symbol, i.instrument_id;
END;
$$;

COMMIT;
