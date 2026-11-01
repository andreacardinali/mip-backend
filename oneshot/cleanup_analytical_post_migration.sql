BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

DO $$
DECLARE
    v_name text;
BEGIN
    FOREACH v_name IN ARRAY ARRAY[
        'event_lake', 'analytical_lake', 'analytical_lake_latest',
        'analytical_event_evidence', 'market_analysis_scan_events',
        'market_analysis_signal_lake', 'market_analysis_lifecycle_events',
        'trading_signals'
    ] LOOP
        IF to_regclass('public.' || v_name) IS NULL THEN
            RAISE EXCEPTION 'Required post-cutover relation is missing: %', v_name;
        END IF;
    END LOOP;
    FOREACH v_name IN ARRAY ARRAY[
        'market_analysis_signal_events', 'signals', 'signal_history', 'signal_factors'
    ] LOOP
        IF to_regclass('public.' || v_name) IS NOT NULL THEN
            RAISE EXCEPTION 'Legacy relation still exists; finish cutover first: %', v_name;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND routine.prosrc ~ '\m(market_analysis_signal_events|signals|signal_history|signal_factors)\M'
    ) THEN
        RAISE EXCEPTION 'A stored routine still refers to an obsolete table';
    END IF;
    IF strpos(pg_get_viewdef('public.market_analysis_lifecycle_events'::regclass),
        'analytical_lake') = 0 THEN
        RAISE EXCEPTION 'Lifecycle history is not projected from the analytical lake';
    END IF;
    IF NOT EXISTS (
        SELECT FROM pg_trigger
        WHERE tgrelid = 'public.market_analysis_lifecycle_events'::regclass
          AND tgfoid = 'public.trg_market_analysis_archive_lifecycle()'::regprocedure
          AND NOT tgisinternal AND tgenabled = 'O' AND tgtype = 69
    ) OR NOT EXISTS (
        SELECT FROM pg_trigger
        WHERE tgrelid = 'public.trading_signals'::regclass
          AND tgfoid = 'public.trg_market_analysis_capture_lake()'::regprocedure
          AND NOT tgisinternal AND tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION 'Required analytical persistence triggers are not enabled';
    END IF;
    IF EXISTS (
        SELECT FROM public.event_lake
        WHERE event_type IN ('MARKET_OBSERVATION', 'SIGNAL_ASSESSMENT',
            'SIGNAL_ENRICHMENT', 'SIGNAL_LIFECYCLE')
    ) THEN
        RAISE EXCEPTION 'Analytical events remain in the informational lake';
    END IF;
    IF EXISTS (
        SELECT FROM public.analytical_lake lake
        WHERE lake.provenance->>'operation' = 'legacy_lifecycle_backfill'
          AND (lake.payload->'lifecycle_event' IS DISTINCT FROM lake.raw_payload
            OR NOT EXISTS (
                SELECT FROM public.market_analysis_lifecycle_events event
                WHERE event = jsonb_populate_record(
                    NULL::public.market_analysis_lifecycle_events, lake.raw_payload)
            ) OR NOT EXISTS (
                SELECT FROM public.market_analysis_signal_lake link
                WHERE link.event_id = lake.event_id
                  AND link.signal_id = (lake.payload->>'signal_id')::uuid
            ))
    ) OR EXISTS (
        SELECT FROM public.market_analysis_lifecycle_events
        GROUP BY signal_event_id HAVING count(*) <> 1
    ) THEN
        RAISE EXCEPTION 'Archived lifecycle records or signal links are inconsistent';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS public.trg_market_analysis_capture_performance_price() RESTRICT;

ANALYZE public.event_lake;
ANALYZE public.analytical_lake;
ANALYZE public.analytical_event_evidence;
ANALYZE public.market_analysis_scan_events;
ANALYZE public.market_analysis_signal_lake;
ANALYZE public.trading_signals;

SELECT event_type, count(*) AS archived_events
FROM public.analytical_lake
GROUP BY event_type ORDER BY event_type;
COMMIT;