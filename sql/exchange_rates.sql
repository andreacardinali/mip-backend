BEGIN;

CREATE OR REPLACE FUNCTION sp_upsert_exchange_rate
(
    p_currency_code char(3),
    p_rate_to_usd numeric
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_currency_code char(3);
BEGIN
    v_currency_code := UPPER(TRIM(p_currency_code));

    IF v_currency_code IS NULL
       OR LENGTH(v_currency_code) <> 3
    THEN
        RAISE EXCEPTION
            'Invalid currency code: %',
            p_currency_code;
    END IF;

    IF p_rate_to_usd IS NULL
       OR p_rate_to_usd <= 0
    THEN
        RAISE EXCEPTION
            'Invalid exchange rate for %: %',
            v_currency_code,
            p_rate_to_usd;
    END IF;

    INSERT INTO exchange_rates
    (
        currency_code,
        rate_to_usd,
        updated_at
    )
    VALUES
    (
        v_currency_code,
        p_rate_to_usd,
        now()
    )
    ON CONFLICT (currency_code)
    DO UPDATE
    SET
        rate_to_usd = EXCLUDED.rate_to_usd,
        updated_at = now();
END;
$$;


CREATE OR REPLACE FUNCTION fx_convert
(
    p_amount numeric,
    p_from char(3),
    p_to char(3)
)
RETURNS numeric
LANGUAGE plpgsql
AS $$
DECLARE
    v_from_currency char(3);
    v_to_currency char(3);
    v_from_rate numeric;
    v_to_rate numeric;
BEGIN
    IF p_amount IS NULL THEN
        RETURN NULL;
    END IF;

    v_from_currency := UPPER(TRIM(p_from));
    v_to_currency := UPPER(TRIM(p_to));

    IF v_from_currency = v_to_currency THEN
        RETURN p_amount;
    END IF;

    SELECT er.rate_to_usd
    INTO v_from_rate
    FROM exchange_rates er
    WHERE er.currency_code = v_from_currency;

    IF v_from_rate IS NULL THEN
        RAISE EXCEPTION
            'Exchange rate not found for %',
            v_from_currency;
    END IF;

    SELECT er.rate_to_usd
    INTO v_to_rate
    FROM exchange_rates er
    WHERE er.currency_code = v_to_currency;

    IF v_to_rate IS NULL THEN
        RAISE EXCEPTION
            'Exchange rate not found for %',
            v_to_currency;
    END IF;

    RETURN
        (p_amount * v_from_rate)
        / v_to_rate;
END;
$$;

COMMIT;
