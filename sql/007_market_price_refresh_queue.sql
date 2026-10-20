BEGIN;

CREATE TABLE IF NOT EXISTS market_price_refresh_queue
(
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
    symbol varchar(50) NOT NULL,
    data_source varchar(50) NOT NULL DEFAULT 'YAHOO',
    status varchar(20) NOT NULL DEFAULT 'PENDING',
    attempt_count integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 6,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    locked_by varchar(150),
    last_error text,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_market_price_refresh_status
        CHECK (status IN ('PENDING', 'PROCESSING', 'RETRY', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_market_price_refresh_attempts
        CHECK (attempt_count >= 0 AND max_attempts > 0),
    CONSTRAINT uq_market_price_refresh_instrument UNIQUE (instrument_id)
);

CREATE INDEX IF NOT EXISTS ix_market_price_refresh_due
ON market_price_refresh_queue(status, next_attempt_at)
WHERE status IN ('PENDING', 'RETRY');

CREATE OR REPLACE FUNCTION sp_enqueue_market_price_refresh(
    p_instrument_id uuid,
    p_symbol varchar,
    p_data_source varchar
)
RETURNS uuid
LANGUAGE plpgsql
AS $function$
DECLARE
    v_job_id uuid;
BEGIN
    INSERT INTO market_price_refresh_queue
    (
        instrument_id,
        symbol,
        data_source,
        status,
        attempt_count,
        next_attempt_at,
        locked_at,
        locked_by,
        last_error,
        completed_at,
        updated_at
    )
    VALUES
    (
        p_instrument_id,
        UPPER(TRIM(p_symbol)),
        COALESCE(NULLIF(UPPER(TRIM(p_data_source)), ''), 'YAHOO'),
        'PENDING',
        0,
        now(),
        NULL,
        NULL,
        NULL,
        NULL,
        now()
    )
    ON CONFLICT (instrument_id)
    DO UPDATE SET
        symbol = EXCLUDED.symbol,
        data_source = EXCLUDED.data_source,
        status = CASE
            WHEN market_price_refresh_queue.status = 'PROCESSING'
                THEN market_price_refresh_queue.status
            ELSE 'PENDING'
        END,
        attempt_count = CASE
            WHEN market_price_refresh_queue.status = 'PROCESSING'
                THEN market_price_refresh_queue.attempt_count
            ELSE 0
        END,
        next_attempt_at = CASE
            WHEN market_price_refresh_queue.status = 'PROCESSING'
                THEN market_price_refresh_queue.next_attempt_at
            ELSE now()
        END,
        locked_at = CASE
            WHEN market_price_refresh_queue.status = 'PROCESSING'
                THEN market_price_refresh_queue.locked_at
            ELSE NULL
        END,
        locked_by = CASE
            WHEN market_price_refresh_queue.status = 'PROCESSING'
                THEN market_price_refresh_queue.locked_by
            ELSE NULL
        END,
        last_error = NULL,
        completed_at = NULL,
        updated_at = now()
    RETURNING job_id INTO v_job_id;

    RETURN v_job_id;
END;
$function$;

COMMIT;
