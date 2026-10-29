BEGIN;

ALTER TABLE public.market_analysis_signal_events
ADD COLUMN IF NOT EXISTS performance_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp();

CREATE OR REPLACE FUNCTION public.sp_market_analysis_performance_price(
    p_instrument_id uuid, p_observed_at timestamptz, p_payload jsonb,
    p_use_exit_signal boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_price numeric;
    v_price_at timestamptz;
    v_source text := 'UNAVAILABLE';
BEGIN
    IF jsonb_typeof(p_payload->'reference_price') = 'number' THEN
        v_price := (p_payload->>'reference_price')::numeric;
        IF v_price > 0 THEN
            v_price_at := p_observed_at;
            v_source := 'SIGNAL_REFERENCE';
        END IF;
    END IF;
    IF v_source = 'UNAVAILABLE' AND p_use_exit_signal
       AND p_payload->>'exit_signal_id' IS NOT NULL THEN
        SELECT reference_price, observed_at INTO v_price, v_price_at
        FROM public.trading_signals
        WHERE signal_id::text = p_payload->>'exit_signal_id'
          AND instrument_id = p_instrument_id
          AND observed_at <= p_observed_at AND reference_price > 0;
        IF FOUND THEN v_source := 'EXIT_SIGNAL_REFERENCE'; END IF;
    END IF;
    IF v_source = 'UNAVAILABLE' THEN
        SELECT close, ts INTO v_price, v_price_at
        FROM public.price_history
        WHERE instrument_id = p_instrument_id AND ts <= p_observed_at AND close > 0
        ORDER BY ts DESC, price_id DESC LIMIT 1;
        IF FOUND THEN v_source := 'PRICE_HISTORY_ASOF'; END IF;
    END IF;
    IF v_source = 'UNAVAILABLE' THEN v_price := NULL; v_price_at := NULL; END IF;
    RETURN jsonb_build_object('price', v_price, 'price_at', v_price_at, 'source', v_source);
END;
$$;

CREATE OR REPLACE FUNCTION public.trg_market_analysis_capture_performance_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.payload := NEW.payload || jsonb_build_object('performance',
        public.sp_market_analysis_performance_price(
            NEW.instrument_id, NEW.observed_at, NEW.payload, true
        ) || jsonb_build_object('historical_backfill', false));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_market_analysis_capture_performance_price
BEFORE INSERT ON public.market_analysis_signal_events
FOR EACH ROW EXECUTE FUNCTION public.trg_market_analysis_capture_performance_price();

UPDATE public.market_analysis_signal_events
SET performance_recorded_at = COALESCE((payload->>'processed_at')::timestamptz, created_at),
    payload = payload || jsonb_build_object('performance',
    public.sp_market_analysis_performance_price(instrument_id, observed_at, payload, false)
    || jsonb_build_object('historical_backfill', true))
WHERE NOT payload ? 'performance';

CREATE INDEX IF NOT EXISTS ix_signal_events_performance_cycles
ON public.market_analysis_signal_events (instrument_id, strategy_code, timeframe, performance_recorded_at, signal_event_id);

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_signal_performance(
    p_instrument_id uuid DEFAULT NULL, p_strategy_code varchar DEFAULT NULL,
    p_status varchar DEFAULT NULL, p_limit integer DEFAULT 100, p_offset integer DEFAULT 0
) RETURNS SETOF jsonb LANGUAGE sql STABLE AS $$
    WITH cycles AS (
        SELECT entry.*, terminal.event_type AS end_reason,
            terminal.observed_at AS exit_at, terminal.payload AS exit_payload
        FROM public.market_analysis_signal_events entry
        LEFT JOIN LATERAL (
            SELECT event_type, observed_at, payload
            FROM public.market_analysis_signal_events next_event
            WHERE next_event.instrument_id = entry.instrument_id
              AND next_event.strategy_code = entry.strategy_code
              AND next_event.timeframe = entry.timeframe
              AND (next_event.performance_recorded_at, next_event.signal_event_id) >
                  (entry.performance_recorded_at, entry.signal_event_id)
              AND next_event.event_type IN ('CREATED', 'DIRECTION_CHANGED', 'CLOSED', 'EXPIRED')
            ORDER BY next_event.performance_recorded_at, next_event.signal_event_id LIMIT 1
        ) terminal ON true
        WHERE entry.event_type IN ('CREATED', 'DIRECTION_CHANGED')
          AND entry.new_direction IN ('LONG', 'SHORT')
          AND (p_instrument_id IS NULL OR entry.instrument_id = p_instrument_id)
          AND (p_strategy_code IS NULL OR entry.strategy_code = p_strategy_code)
          AND (p_status IS NULL OR p_status = CASE WHEN terminal.event_type IS NULL THEN 'OPEN' ELSE 'CLOSED' END)
        ORDER BY entry.performance_recorded_at DESC, entry.signal_event_id DESC
        LIMIT LEAST(GREATEST(p_limit, 1), 1000) OFFSET GREATEST(p_offset, 0)
    ), valued AS (
        SELECT cycles.*, i.symbol, i.exchange, i.currency_code,
            (payload->'performance'->>'price')::numeric AS entry_price,
            CASE WHEN end_reason IS NOT NULL THEN exit_payload->'performance'
                 ELSE jsonb_build_object('price', mark.close, 'price_at', mark.ts, 'source',
                     CASE WHEN mark.close IS NULL THEN 'UNAVAILABLE' ELSE 'PRICE_HISTORY_ASOF' END)
            END AS valuation
        FROM cycles
        JOIN public.instruments i USING (instrument_id)
        LEFT JOIN LATERAL (
            SELECT close, ts FROM public.price_history
            WHERE instrument_id = cycles.instrument_id AND ts >= cycles.observed_at
              AND ts <= CURRENT_TIMESTAMP AND close > 0 AND cycles.end_reason IS NULL
            ORDER BY ts DESC, price_id DESC LIMIT 1
        ) mark ON true
    )
    SELECT jsonb_build_object(
        'performance_id', signal_event_id, 'signal_id', signal_id,
        'instrument_id', instrument_id, 'symbol', symbol, 'exchange', exchange,
        'currency_code', currency_code, 'strategy_code', strategy_code, 'timeframe', timeframe,
        'direction', new_direction, 'status', CASE WHEN end_reason IS NULL THEN 'OPEN' ELSE 'CLOSED' END,
        'end_reason', end_reason, 'entry_at', observed_at, 'exit_at', exit_at,
        'entry_price', entry_price, 'valuation_price', (valuation->>'price')::numeric,
        'entry_price_at', payload->'performance'->'price_at',
        'valuation_price_at', valuation->'price_at',
        'entry_price_source', payload->'performance'->>'source',
        'valuation_price_source', valuation->>'source',
        'historical_backfill', COALESCE((payload->'performance'->>'historical_backfill')::boolean, false),
        'return_pct', CASE WHEN entry_price > 0 AND (valuation->>'price')::numeric > 0
            AND (valuation->>'price_at')::timestamptz >= observed_at
            AND (exit_at IS NULL OR exit_at >= observed_at)
            THEN ((valuation->>'price')::numeric / entry_price - 1) * 100
                * CASE WHEN new_direction = 'SHORT' THEN -1 ELSE 1 END ELSE NULL END
    ) FROM valued ORDER BY performance_recorded_at DESC, signal_event_id DESC;
$$;

COMMIT;