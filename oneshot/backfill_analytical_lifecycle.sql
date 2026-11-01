BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.market_analysis_signal_events IN SHARE MODE;
LOCK TABLE public.trading_signals, public.instruments IN SHARE MODE;

DO $$
BEGIN
    IF to_regprocedure('public.sp_save_analytical_event(jsonb)') IS NULL THEN
        RAISE EXCEPTION 'Install the analytical lake before running the backfill';
    END IF;
END;
$$;

CREATE TEMP TABLE analytical_lifecycle_backfill_result (
    signal_event_id uuid PRIMARY KEY,
    event_id uuid NOT NULL,
    original_record jsonb NOT NULL
) ON COMMIT DROP;

DO $$
DECLARE
    v_row record;
    v_record jsonb;
    v_subjects jsonb;
    v_event_id uuid;
BEGIN
    FOR v_row IN
        SELECT e.*, i.entity_id
        FROM public.market_analysis_signal_events e
        JOIN public.instruments i USING (instrument_id)
        ORDER BY e.performance_recorded_at, e.signal_event_id
    LOOP
        v_record := to_jsonb(v_row) - 'entity_id';
        v_subjects := jsonb_build_array(jsonb_build_object(
            'instrument_id', v_row.instrument_id, 'entity_id', v_row.entity_id));
        v_event_id := public.sp_save_analytical_event(jsonb_build_object(
            'schema_version', 1, 'payload_version', 1,
            'event_type', 'SIGNAL_LIFECYCLE', 'stage', 'VALIDATED',
            'source', 'MIP', 'event_key', 'legacy-lifecycle:' || v_row.signal_event_id,
            'entity_id', v_row.entity_id,
            'occurred_at', v_row.observed_at, 'observed_at', v_row.created_at,
            'event_day', (v_row.observed_at AT TIME ZONE 'UTC')::date,
            'subjects', v_subjects, 'parent_event_ids', '[]'::jsonb,
            'payload', jsonb_build_object(
                'signal_id', v_row.signal_id,
                'signal', jsonb_build_object(
                    'signal_id', v_row.signal_id, 'instrument_id', v_row.instrument_id,
                    'strategy_code', v_row.strategy_code, 'timeframe', v_row.timeframe,
                    'status', v_row.new_status, 'direction', v_row.new_direction,
                    'observed_at', v_row.observed_at),
                'lifecycle_event', v_record, 'legacy_without_assessment', true),
            'evidence', '{}'::jsonb,
            'provenance', jsonb_build_object(
                'operation', 'legacy_lifecycle_backfill',
                'original_signal_event_id', v_row.signal_event_id),
            'raw_payload', v_record
        ));
        INSERT INTO public.market_analysis_signal_lake (signal_id, event_id)
        VALUES (v_row.signal_id, v_event_id) ON CONFLICT DO NOTHING;
        INSERT INTO analytical_lifecycle_backfill_result
        VALUES (v_row.signal_event_id, v_event_id, v_record);
    END LOOP;
    IF (SELECT count(*) FROM analytical_lifecycle_backfill_result)
        <> (SELECT count(*) FROM public.market_analysis_signal_events) THEN
        RAISE EXCEPTION 'Backfill did not cover every legacy lifecycle record';
    END IF;
    IF EXISTS (
        SELECT FROM analytical_lifecycle_backfill_result r
        LEFT JOIN public.analytical_lake a USING (event_id)
        WHERE a.payload->'lifecycle_event' IS DISTINCT FROM r.original_record
            OR NOT EXISTS (
                SELECT FROM public.market_analysis_signal_lake link
                WHERE link.event_id = r.event_id
                  AND link.signal_id = (r.original_record->>'signal_id')::uuid)
    ) THEN
        RAISE EXCEPTION 'Backfill payload or signal association verification failed';
    END IF;
END;
$$;

SELECT count(*) AS verified_legacy_records
FROM analytical_lifecycle_backfill_result;

COMMIT;