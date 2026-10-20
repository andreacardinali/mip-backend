BEGIN;

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS openfigi_exch_code varchar(10);

-- The first OpenFIGI rollout copied exchCode into exchange. exchCode may be a
-- broad/composite market code such as US, not a trading venue. Remove only the
-- values written by that rollout where no MIC was resolved.
UPDATE instruments
SET
    openfigi_exch_code = COALESCE(openfigi_exch_code, exchange),
    exchange = NULL,
    metadata_refreshed_at = now()
WHERE metadata_source = 'OPENFIGI'
  AND mic_code IS NULL
  AND exchange IS NOT NULL;

CREATE OR REPLACE FUNCTION sp_apply_instrument_reference_metadata(
    p_instrument_id uuid,
    p_payload jsonb
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_figi varchar(20);
BEGIN
    v_figi := NULLIF(UPPER(TRIM(p_payload->>'figi')), '');

    -- Do not move a FIGI already owned by another row. Duplicate legacy
    -- instruments must be reconciled separately; refresh must remain safe.
    IF v_figi IS NOT NULL AND EXISTS (
        SELECT 1
        FROM instruments other
        WHERE other.figi = v_figi
          AND other.instrument_id <> p_instrument_id
    ) THEN
        v_figi := NULL;
    END IF;

    UPDATE instruments
    SET
        exchange = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'mic_code')), ''),
            exchange
        ),
        mic_code = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'mic_code')), ''),
            mic_code
        ),
        openfigi_exch_code = COALESCE(
            NULLIF(UPPER(TRIM(p_payload->>'openfigi_exch_code')), ''),
            openfigi_exch_code
        ),
        figi = COALESCE(v_figi, figi),
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

COMMIT;
