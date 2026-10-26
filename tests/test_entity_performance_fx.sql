BEGIN;

DO $$
DECLARE
    v_usd numeric;
    v_eur numeric;
BEGIN
    PERFORM public.sp_upsert_exchange_rate_history('USD', DATE '2026-01-02', 1.00000000);
    PERFORM public.sp_upsert_exchange_rate_history('EUR', DATE '2026-01-02', 1.10000000);
    PERFORM public.sp_upsert_exchange_rate_history('USD', DATE '2026-01-03', 1.00000000);
    PERFORM public.sp_upsert_exchange_rate_history('EUR', DATE '2026-01-03', 1.20000000);

    v_usd := public.fx_convert_at_date(100, 'USD', 'USD', DATE '2026-01-03');
    v_eur := public.fx_convert_at_date(100, 'EUR', 'USD', DATE '2026-01-03');

    IF v_usd <> 100 THEN
        RAISE EXCEPTION 'USD identity conversion failed: %', v_usd;
    END IF;

    IF v_eur <> 120 THEN
        RAISE EXCEPTION 'EUR/USD historical conversion failed: %', v_eur;
    END IF;
END;
$$;

-- Structural checks. The functional entity test is intentionally left to the
-- application fixture because entity, user and identity UUIDs are deployment data.
DO $$
BEGIN
    IF to_regclass('public.exchange_rate_history') IS NULL THEN
        RAISE EXCEPTION 'exchange_rate_history was not created';
    END IF;

    IF to_regprocedure(
        'public.sp_get_entity_performance(uuid,uuid,date,date,character)'
    ) IS NULL THEN
        RAISE EXCEPTION 'sp_get_entity_performance signature is missing';
    END IF;
END;
$$;

ROLLBACK;
