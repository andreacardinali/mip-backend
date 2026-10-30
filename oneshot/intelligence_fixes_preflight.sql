SET LOCAL lock_timeout = '10s';

ALTER TABLE public.trading_signals
    ADD COLUMN IF NOT EXISTS last_llm_analysis_at timestamptz;

ALTER TABLE public.positions
    ADD COLUMN IF NOT EXISTS cost_basis_amount numeric(20,8),
    ADD COLUMN IF NOT EXISTS cost_basis_currency character(3);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.positions'::regclass
          AND conname = 'ck_positions_cost_basis'
    ) THEN
        ALTER TABLE public.positions ADD CONSTRAINT ck_positions_cost_basis CHECK (
            (cost_basis_amount IS NULL AND cost_basis_currency IS NULL)
            OR (cost_basis_amount IS NOT NULL AND cost_basis_amount >= 0
                AND cost_basis_currency IS NOT NULL)
        );
    END IF;
END;
$$;