BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.trading_signals, public.instruments,
    public.market_analysis_signal_events, public.signals,
    public.signal_history, public.signal_factors IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.event_lake, public.analytical_lake,
    public.market_analysis_signal_lake IN ACCESS EXCLUSIVE MODE;

CREATE TEMP TABLE analytical_cutover_routines (
    routine regprocedure PRIMARY KEY,
    body_hash text NOT NULL
) ON COMMIT DROP;
INSERT INTO analytical_cutover_routines VALUES
    ('public.sp_alert_list_rule_candidates(uuid,integer)', 'bc3634543ea14674e9c5795a7960acc7'),
    ('public.sp_consolidate_entity(uuid,uuid,boolean)', 'c67124b47684e07acbc14aceb6b51819'),
    ('public.sp_market_analysis_close_missing_signals(timestamptz,integer)', '30fc183f64325b491dccb8be5913cbce'),
    ('public.sp_market_analysis_expire_evaluated_signals(jsonb,uuid[],integer)', 'a2b5be57573947f0296304daf8f2c459'),
    ('public.sp_market_analysis_list_signal_events(uuid,varchar,integer)', '3bf8e6e1778e8a16de095e36588a5431'),
    ('public.sp_market_analysis_list_signal_performance(uuid,varchar,varchar,integer,integer)', 'd8a77d26e264f6a432e3f0d9cf725b05'),
    ('public.sp_market_analysis_reconcile_signal_lifecycle(uuid,integer)', 'd3350180a46ffd726cb64e0a48c3ee33');

DO $$
BEGIN
    IF EXISTS (SELECT FROM public.signals)
        OR EXISTS (SELECT FROM public.signal_history)
        OR EXISTS (SELECT FROM public.signal_factors) THEN
        RAISE EXCEPTION 'Obsolete signal tables are not empty; do not discard them';
    END IF;
    IF EXISTS (
        SELECT FROM analytical_cutover_routines expected
        JOIN pg_proc actual ON actual.oid = expected.routine
        WHERE md5(actual.prosrc) <> expected.body_hash
    ) OR EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public' AND routine.prokind IN ('f', 'p')
          AND routine.prosrc ~ '\m(market_analysis_signal_events|signals|signal_history|signal_factors)\M'
          AND NOT EXISTS (SELECT FROM analytical_cutover_routines expected
              WHERE expected.routine = routine.oid)
    ) THEN
        RAISE EXCEPTION 'Legacy routine inventory changed; review the cutover first';
    END IF;
    IF EXISTS (
        SELECT FROM public.market_analysis_signal_events original
        WHERE (SELECT count(*) FROM public.analytical_lake lake
            JOIN public.market_analysis_signal_lake link ON link.event_id = lake.event_id
            WHERE lake.event_type = 'SIGNAL_LIFECYCLE' AND lake.source = 'MIP'
              AND lake.event_key = 'legacy-lifecycle:' || original.signal_event_id
              AND lake.payload->'lifecycle_event' = to_jsonb(original)
              AND lake.raw_payload = to_jsonb(original)
              AND link.signal_id = original.signal_id) <> 1
    ) THEN
        RAISE EXCEPTION 'Run and verify a complete, unchanged lifecycle backfill first';
    END IF;
END;
$$;

CREATE VIEW public.market_analysis_lifecycle_events AS
SELECT record.*
FROM public.analytical_lake lake
CROSS JOIN LATERAL jsonb_to_record(lake.payload->'lifecycle_event') AS record(
    signal_event_id uuid, signal_id uuid, instrument_id uuid,
    strategy_code varchar, timeframe varchar,
    previous_status varchar, new_status varchar,
    previous_direction varchar, new_direction varchar,
    event_type varchar, reason varchar, observed_at timestamptz,
    payload jsonb, created_at timestamptz, updated_at timestamptz,
    performance_recorded_at timestamptz
)
WHERE lake.event_type = 'SIGNAL_LIFECYCLE'
  AND lake.payload ? 'lifecycle_event';

CREATE FUNCTION public.trg_market_analysis_archive_lifecycle()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    v_signal public.trading_signals%ROWTYPE;
    v_snapshot jsonb;
    v_entity_id uuid;
    v_assessment_id uuid;
    v_parents jsonb := '[]'::jsonb;
    v_event_id uuid;
BEGIN
    SELECT * INTO STRICT v_signal FROM public.trading_signals
    WHERE signal_id = NEW.signal_id FOR UPDATE;
    IF NEW.instrument_id IS DISTINCT FROM v_signal.instrument_id
        OR NEW.strategy_code IS DISTINCT FROM v_signal.strategy_code
        OR NEW.timeframe IS DISTINCT FROM v_signal.timeframe THEN
        RAISE EXCEPTION 'Lifecycle transition must match its exact trading signal';
    END IF;
    IF NEW.event_type IS NULL
        OR NEW.event_type NOT IN ('CREATED', 'OBSERVED', 'CONFIRMED',
            'DIRECTION_CHANGED', 'CLOSED', 'EXPIRED')
        OR NEW.new_status IS NULL OR NEW.new_direction IS NULL
        OR NEW.observed_at IS NULL THEN
        RAISE EXCEPTION 'Incomplete lifecycle transition';
    END IF;
    NEW.signal_event_id := coalesce(NEW.signal_event_id, gen_random_uuid());
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'analytical-lifecycle:' || NEW.signal_event_id, 0));
    IF EXISTS (SELECT FROM public.analytical_lake
        WHERE event_type = 'SIGNAL_LIFECYCLE'
          AND payload->'lifecycle_event'->>'signal_event_id' = NEW.signal_event_id::text) THEN
        RAISE EXCEPTION 'Lifecycle event ID already archived: %', NEW.signal_event_id;
    END IF;
    NEW.created_at := clock_timestamp();
    NEW.updated_at := NEW.created_at;
    NEW.performance_recorded_at := NEW.created_at;
    NEW.payload := coalesce(NEW.payload, '{}'::jsonb);
    NEW.payload := NEW.payload || jsonb_build_object('performance',
        public.sp_market_analysis_performance_price(
            NEW.instrument_id, NEW.observed_at, NEW.payload, true)
        || jsonb_build_object('historical_backfill', false));
    SELECT entity_id INTO v_entity_id FROM public.instruments
    WHERE instrument_id = NEW.instrument_id;
    v_assessment_id := (v_signal.evidence->>'assessment_event_id')::uuid;
    IF v_assessment_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT FROM public.analytical_lake
            WHERE event_id = v_assessment_id AND event_type = 'SIGNAL_ASSESSMENT'
              AND subjects @> jsonb_build_array(jsonb_build_object(
                  'instrument_id', NEW.instrument_id::text))) THEN
            RAISE EXCEPTION 'Lifecycle transition requires its exact instrument assessment';
        END IF;
        v_parents := jsonb_build_array(v_assessment_id);
    END IF;
    v_snapshot := (to_jsonb(v_signal) - ARRAY['created_at', 'updated_at'])
        || jsonb_build_object('status', NEW.new_status, 'direction', NEW.new_direction);
    v_event_id := public.sp_save_analytical_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', 'SIGNAL_LIFECYCLE', 'stage', 'VALIDATED', 'source', 'MIP',
        'event_key', 'lifecycle:' || NEW.signal_event_id,
        'occurred_at', NEW.observed_at, 'observed_at', NEW.created_at,
        'event_day', (NEW.observed_at AT TIME ZONE 'UTC')::date,
        'subjects', jsonb_build_array(jsonb_build_object(
            'instrument_id', NEW.instrument_id, 'entity_id', v_entity_id)),
        'parent_event_ids', v_parents,
        'payload', jsonb_build_object('signal_id', NEW.signal_id,
            'signal', v_snapshot, 'lifecycle_event', to_jsonb(NEW),
            'legacy_without_assessment', v_assessment_id IS NULL),
        'evidence', '{}'::jsonb, 'provenance', '{}'::jsonb));
    INSERT INTO public.market_analysis_signal_lake (signal_id, event_id)
    VALUES (NEW.signal_id, v_event_id) ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_market_analysis_archive_lifecycle
INSTEAD OF INSERT ON public.market_analysis_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION public.trg_market_analysis_archive_lifecycle();

DO $$
DECLARE
    v_routine regprocedure;
    v_definition text;
    v_start integer;
    v_end integer;
BEGIN
    FOR v_routine IN SELECT routine FROM analytical_cutover_routines LOOP
        v_definition := replace(pg_get_functiondef(v_routine), E'\r\n', E'\n');
        IF v_routine = 'public.sp_consolidate_entity(uuid,uuid,boolean)'::regprocedure THEN
            v_start := strpos(v_definition, '    UPDATE signal_factors');
            v_end := strpos(v_definition, '    UPDATE portfolio_impacts');
            IF v_start = 0 OR v_end <= v_start THEN
                RAISE EXCEPTION 'Cannot locate the obsolete entity consolidation block';
            END IF;
            v_definition := substr(v_definition, 1, v_start - 1)
                || substr(v_definition, v_end);
            v_start := strpos(v_definition, '    IF NOT p_force');
            IF v_start = 0 THEN
                RAISE EXCEPTION 'Cannot locate entity consolidation preconditions';
            END IF;
            v_definition := substr(v_definition, 1, v_start - 1) || $guard$
    IF EXISTS (SELECT FROM public.analytical_lake
        WHERE entity_id = p_source_entity_id) THEN
        RAISE EXCEPTION 'Cannot consolidate an entity referenced by immutable analytical history';
    END IF;

$guard$ || substr(v_definition, v_start);
        ELSE
            v_definition := replace(v_definition,
                'public.market_analysis_signal_events',
                'public.market_analysis_lifecycle_events');
        END IF;
        EXECUTE v_definition;
    END LOOP;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public' AND routine.prokind IN ('f', 'p')
          AND routine.prosrc ~ '\m(market_analysis_signal_events|signals|signal_history|signal_factors)\M'
    ) THEN
        RAISE EXCEPTION 'A stored routine still refers to an obsolete table';
    END IF;
    IF EXISTS (
        SELECT FROM public.market_analysis_signal_events original
        FULL JOIN public.market_analysis_lifecycle_events archived USING (signal_event_id)
        WHERE original.signal_event_id IS NULL
            OR archived.signal_event_id IS NULL
            OR to_jsonb(original) IS DISTINCT FROM to_jsonb(archived)
    ) THEN
        RAISE EXCEPTION 'Lake projection does not preserve the complete original history';
    END IF;
END;
$$;

DROP TABLE public.market_analysis_signal_events RESTRICT;
DROP FUNCTION public.trg_market_analysis_capture_performance_price() RESTRICT;
DROP TABLE public.signals, public.signal_history, public.signal_factors RESTRICT;

SELECT count(*) AS lifecycle_records FROM public.market_analysis_lifecycle_events;
COMMIT;