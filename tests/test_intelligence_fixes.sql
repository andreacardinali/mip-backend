BEGIN;

DO $$
BEGIN
    IF public.position_cost_basis(10, 20, 'USD', NULL, NULL, 'EUR') IS NOT NULL THEN
        RAISE EXCEPTION 'Unknown acquisition FX must not produce an invented EUR cost';
    END IF;
    IF public.position_cost_basis(10, 20, 'USD', NULL, NULL, 'USD') IS DISTINCT FROM 200 THEN
        RAISE EXCEPTION 'Same-currency average acquisition cost must remain valid';
    END IF;
    IF public.position_cost_basis(10, 20, 'USD', 175, 'EUR', 'EUR') IS DISTINCT FROM 175 THEN
        RAISE EXCEPTION 'Explicit historical cost must override average acquisition cost';
    END IF;
    IF public.position_cost_basis(10, 20, 'USD', 175, 'EUR', 'GBP') IS NOT NULL THEN
        RAISE EXCEPTION 'Historical cost must not be converted at current FX';
    END IF;
    IF public.position_cost_basis(10, 20, 'USD', 0, 'EUR', 'EUR') IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'An explicit zero cost must not be treated as missing';
    END IF;
    IF 250 - public.position_cost_basis(10, 20, 'USD', 175, 'EUR', 'EUR') IS DISTINCT FROM 75 THEN
        RAISE EXCEPTION 'Reporting P&L must use historical cost';
    END IF;
END;
$$;

ROLLBACK;