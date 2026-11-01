BEGIN;

DO $$
BEGIN
    IF to_regclass('public.analytical_lake') IS NOT NULL THEN
        RAISE EXCEPTION 'Analytical lake is already installed; do not rerun this installer';
    END IF;
    IF EXISTS (SELECT FROM public.event_lake
        WHERE event_type IN ('MARKET_OBSERVATION', 'SIGNAL_ASSESSMENT',
            'SIGNAL_ENRICHMENT', 'SIGNAL_LIFECYCLE')) THEN
        RAISE EXCEPTION 'Existing analytical events need an explicit transfer before installation';
    END IF;
END;
$$;

CREATE TABLE public.analytical_lake (
    LIKE public.event_lake INCLUDING DEFAULTS INCLUDING CONSTRAINTS
);

ALTER TABLE public.analytical_lake
    ADD PRIMARY KEY (event_id),
    ADD FOREIGN KEY (event_type, payload_version)
        REFERENCES public.event_type_catalog (event_type, payload_version),
    ADD FOREIGN KEY (supersedes_event_id)
        REFERENCES public.analytical_lake (event_id),
    ADD FOREIGN KEY (entity_id) REFERENCES public.entities (entity_id);

CREATE UNIQUE INDEX analytical_lake_revision_idx
    ON public.analytical_lake (event_type, stage, source, event_key, entity_id, revision)
    NULLS NOT DISTINCT;
CREATE INDEX analytical_lake_observed_idx ON public.analytical_lake (observed_at);
CREATE INDEX analytical_lake_subjects_idx ON public.analytical_lake USING gin (subjects);
CREATE INDEX analytical_lake_parents_idx ON public.analytical_lake USING gin (parent_event_ids);

INSERT INTO public.event_type_catalog
    (event_type, payload_version, allowed_stages, required_payload_types)
VALUES
    ('MARKET_OBSERVATION', 1, ARRAY['ACQUIRED'],
        '{"snapshot":"object","profile":"object"}'::jsonb),
    ('SIGNAL_ASSESSMENT', 1, ARRAY['VALIDATED'],
        '{"outcome":"string","candidates":"array","profile":"object"}'::jsonb),
    ('SIGNAL_ENRICHMENT', 1, ARRAY['ENRICHED'],
        '{"signal_id":"string","llm_analysis":"object","enrichment_fingerprint":"string"}'::jsonb),
    ('SIGNAL_LIFECYCLE', 1, ARRAY['VALIDATED'],
        '{"signal_id":"string","signal":"object"}'::jsonb);

CREATE VIEW public.analytical_lake_latest AS
SELECT DISTINCT ON (event_type, stage, source, event_key, entity_id) *
FROM public.analytical_lake
ORDER BY event_type, stage, source, event_key, entity_id, revision DESC;

CREATE TABLE public.analytical_event_evidence (
    analytical_event_id uuid NOT NULL REFERENCES public.analytical_lake ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES public.event_lake,
    PRIMARY KEY (analytical_event_id, event_id)
);

CREATE TABLE public.market_analysis_scan_events (
    run_id uuid NOT NULL REFERENCES public.market_analysis_scan_runs ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES public.analytical_lake ON DELETE CASCADE,
    PRIMARY KEY (run_id, event_id)
);

CREATE FUNCTION public.analytical_lake_timestamps() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('mip.analytical_lake_cleanup', true) IS DISTINCT FROM 'on'
           OR to_regclass('pg_temp.analytical_lake_cleanup_candidates') IS NULL THEN
            RAISE EXCEPTION 'Analytical revisions cannot be deleted';
        END IF;
        IF NOT EXISTS (
            SELECT FROM pg_temp.analytical_lake_cleanup_candidates
            WHERE event_id = OLD.event_id
        ) THEN
            RAISE EXCEPTION 'Analytical revision is outside the cleanup selection';
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'INSERT' THEN
        NEW.created_at := v_now;
        NEW.updated_at := v_now;
        NEW.ingested_at := v_now;
        NEW.last_seen_at := v_now;
    ELSE
        IF (to_jsonb(NEW) - ARRAY['last_seen_at', 'updated_at'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['last_seen_at', 'updated_at']) THEN
            RAISE EXCEPTION 'Analytical content is immutable; insert a revision';
        END IF;
        NEW.last_seen_at := greatest(OLD.last_seen_at, NEW.last_seen_at, v_now);
        NEW.updated_at := v_now;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER analytical_lake_timestamps
BEFORE INSERT OR UPDATE OR DELETE ON public.analytical_lake
FOR EACH ROW EXECUTE FUNCTION public.analytical_lake_timestamps();

CREATE FUNCTION public.sp_save_analytical_event(p_envelope jsonb) RETURNS uuid
LANGUAGE plpgsql SET search_path = pg_catalog, public
SET "TimeZone" = 'UTC' SET "DateStyle" = 'ISO, YMD' AS $$
DECLARE
    v_type public.event_type_catalog%ROWTYPE;
    v_previous public.analytical_lake%ROWTYPE;
    v_field record;
    v_event_id uuid;
    v_entity_id uuid;
    v_subjects jsonb;
    v_parents uuid[];
    v_hash text;
    v_event_type text := p_envelope->>'event_type';
    v_stage text := p_envelope->>'stage';
    v_source text := p_envelope->>'source';
    v_key text := p_envelope->>'event_key';
    v_occurred_at timestamptz := (p_envelope->>'occurred_at')::timestamptz;
    v_event_day date := (p_envelope->>'event_day')::date;
BEGIN
    IF jsonb_typeof(p_envelope) IS DISTINCT FROM 'object'
        OR (p_envelope->>'schema_version')::integer IS DISTINCT FROM 1
        OR nullif(btrim(v_source), '') IS NULL
        OR nullif(btrim(v_key), '') IS NULL
        OR v_event_type IS NULL
        OR v_event_type NOT IN ('MARKET_OBSERVATION', 'SIGNAL_ASSESSMENT',
            'SIGNAL_ENRICHMENT', 'SIGNAL_LIFECYCLE') THEN
        RAISE EXCEPTION 'Invalid analytical envelope';
    END IF;
    SELECT * INTO v_type FROM public.event_type_catalog
    WHERE event_type = v_event_type
      AND payload_version = (p_envelope->>'payload_version')::integer;
    IF NOT FOUND OR v_stage IS NULL OR NOT v_stage = ANY(v_type.allowed_stages) THEN
        RAISE EXCEPTION 'Unregistered analytical type/version or stage';
    END IF;
    FOREACH v_key IN ARRAY ARRAY['payload', 'evidence', 'provenance'] LOOP
        IF jsonb_typeof(p_envelope->v_key) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION '% must be an object', v_key;
        END IF;
    END LOOP;
    v_key := p_envelope->>'event_key';
    IF jsonb_typeof(p_envelope->'subjects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_envelope->'parent_event_ids') IS DISTINCT FROM 'array'
        OR (p_envelope->'raw_payload' <> 'null'::jsonb
            AND jsonb_typeof(p_envelope->'raw_payload') <> 'object') THEN
        RAISE EXCEPTION 'Invalid subjects, parents or raw payload';
    END IF;
    IF EXISTS (SELECT FROM jsonb_array_elements(p_envelope->'subjects') AS item(value)
        WHERE jsonb_typeof(value) <> 'object') THEN
        RAISE EXCEPTION 'Subjects must be objects';
    END IF;
    FOR v_field IN SELECT * FROM jsonb_each_text(v_type.required_payload_types) LOOP
        IF jsonb_typeof(p_envelope->'payload'->v_field.key)
            IS DISTINCT FROM v_field.value THEN
            RAISE EXCEPTION 'Invalid % payload field %', v_event_type, v_field.key;
        END IF;
    END LOOP;
    IF ((p_envelope->>'occurred_at') IS NOT NULL
        AND (p_envelope->>'occurred_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
        OR ((p_envelope->>'observed_at') IS NOT NULL
        AND (p_envelope->>'observed_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$') THEN
        RAISE EXCEPTION 'Instants require an explicit UTC offset';
    END IF;
    IF (SELECT count(DISTINCT value->>'instrument_id')
        FROM jsonb_array_elements(p_envelope->'subjects')) <> 1
        OR EXISTS (SELECT FROM jsonb_array_elements(p_envelope->'subjects')
            WHERE value->>'instrument_id' IS NULL) THEN
        RAISE EXCEPTION 'Analytical events require one exact instrument';
    END IF;
    IF (SELECT count(DISTINCT value->>'entity_id')
        FROM jsonb_array_elements(p_envelope->'subjects')) > 1 THEN
        RAISE EXCEPTION 'Analytical events cannot span entities';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements(p_envelope->'subjects') subject
        LEFT JOIN public.instruments instrument
            ON instrument.instrument_id = (subject->>'instrument_id')::uuid
        WHERE instrument.instrument_id IS NULL
            OR ((subject->>'entity_id') IS NOT NULL AND instrument.entity_id
                IS DISTINCT FROM (subject->>'entity_id')::uuid)
    ) THEN
        RAISE EXCEPTION 'Unknown instrument or inconsistent parent entity';
    END IF;
    SELECT coalesce(jsonb_agg(value ORDER BY value), '[]'::jsonb)
    INTO v_subjects FROM (SELECT DISTINCT value
        FROM jsonb_array_elements(p_envelope->'subjects')) subjects;
    SELECT (value->>'entity_id')::uuid INTO v_entity_id
    FROM jsonb_array_elements(v_subjects) WHERE value->>'entity_id' IS NOT NULL LIMIT 1;
    SELECT coalesce(array_agg(DISTINCT value::uuid ORDER BY value::uuid), '{}'::uuid[])
    INTO v_parents FROM jsonb_array_elements_text(p_envelope->'parent_event_ids');
    LOCK TABLE public.event_lake IN ROW EXCLUSIVE MODE;
    LOCK TABLE public.analytical_lake IN ROW EXCLUSIVE MODE;
    IF EXISTS (SELECT FROM unnest(v_parents) parent(event_id)
        WHERE NOT EXISTS (SELECT FROM public.analytical_lake lake
            WHERE lake.event_id = parent.event_id)
          AND NOT EXISTS (SELECT FROM public.event_lake lake
            WHERE lake.event_id = parent.event_id)) THEN
        RAISE EXCEPTION 'Unknown parent event';
    END IF;
    IF v_event_type = 'SIGNAL_ASSESSMENT' AND (
        p_envelope->'payload'->>'outcome' NOT IN ('SIGNAL', 'NO_SIGNAL')
        OR NOT EXISTS (SELECT FROM public.analytical_lake observation
            WHERE observation.event_id = ANY(v_parents)
              AND observation.event_type = 'MARKET_OBSERVATION'
              AND observation.subjects = v_subjects)
    ) THEN
        RAISE EXCEPTION 'Assessment requires its exact instrument observation';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'schema_version', 1, 'payload_version', v_type.payload_version,
        'source_event_id', p_envelope->>'source_event_id',
        'occurred_at', v_occurred_at, 'event_day', v_event_day,
        'subjects', v_subjects, 'parent_event_ids', v_parents,
        'payload', p_envelope->'payload', 'evidence', p_envelope->'evidence'
    )::text, 'UTF8')), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(jsonb_build_array(
        'analytical', v_event_type, v_stage, v_source, v_key, v_entity_id)::text, 0));
    SELECT * INTO v_previous FROM public.analytical_lake
    WHERE event_type = v_event_type AND stage = v_stage AND source = v_source
      AND event_key = v_key AND entity_id IS NOT DISTINCT FROM v_entity_id
    ORDER BY revision DESC LIMIT 1;
    IF FOUND AND v_previous.content_hash = v_hash THEN
        UPDATE public.analytical_lake SET last_seen_at = clock_timestamp()
        WHERE event_id = v_previous.event_id;
        RETURN v_previous.event_id;
    END IF;
    INSERT INTO public.analytical_lake (
        entity_id, schema_version, event_type, payload_version, stage, source,
        source_event_id, event_key, revision, supersedes_event_id, content_hash,
        occurred_at, event_day, observed_at, subjects, parent_event_ids,
        payload, raw_payload, evidence, provenance
    ) VALUES (
        v_entity_id, 1, v_type.event_type, v_type.payload_version, v_stage, v_source,
        p_envelope->>'source_event_id', v_key, coalesce(v_previous.revision, 0) + 1,
        v_previous.event_id, v_hash, v_occurred_at, v_event_day,
        coalesce((p_envelope->>'observed_at')::timestamptz, clock_timestamp()),
        v_subjects, v_parents, p_envelope->'payload',
        nullif(p_envelope->'raw_payload', 'null'::jsonb),
        p_envelope->'evidence', p_envelope->'provenance'
    ) RETURNING event_id INTO v_event_id;
    INSERT INTO public.analytical_event_evidence (analytical_event_id, event_id)
    SELECT v_event_id, event_id FROM public.event_lake WHERE event_id = ANY(v_parents);
    RETURN v_event_id;
END;
$$;

CREATE FUNCTION public.sp_market_analysis_link_scan_event(p_run_id uuid, p_event_id uuid)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM public.analytical_lake
        WHERE event_id = p_event_id
          AND event_type IN ('MARKET_OBSERVATION', 'SIGNAL_ASSESSMENT')) THEN
        RAISE EXCEPTION 'Scan links require an observation or assessment';
    END IF;
    INSERT INTO public.market_analysis_scan_events VALUES (p_run_id, p_event_id)
    ON CONFLICT DO NOTHING;
END;
$$;

ALTER FUNCTION public.sp_save_event(jsonb) RENAME TO sp_save_informational_event;

CREATE FUNCTION public.sp_save_event(p_envelope jsonb) RETURNS uuid
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_envelope->>'event_type' IN ('MARKET_OBSERVATION', 'SIGNAL_ASSESSMENT',
        'SIGNAL_ENRICHMENT', 'SIGNAL_LIFECYCLE') THEN
        RAISE EXCEPTION 'Analytical events belong in analytical_lake';
    END IF;
    RETURN public.sp_save_informational_event(p_envelope);
END;
$$;

CREATE TABLE public.market_analysis_signal_lake (
    signal_id uuid NOT NULL REFERENCES public.trading_signals ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES public.analytical_lake,
    PRIMARY KEY (signal_id, event_id)
);
CREATE INDEX market_analysis_signal_lake_event_idx
ON public.market_analysis_signal_lake (event_id);

CREATE FUNCTION public.trg_market_analysis_capture_lake() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    v_assessment_id uuid := (NEW.evidence->>'assessment_event_id')::uuid;
    v_event_id uuid;
    v_entity_id uuid;
    v_snapshot jsonb := to_jsonb(NEW) - ARRAY['created_at', 'updated_at'];
BEGIN
    IF TG_OP = 'UPDATE' AND v_snapshot =
        (to_jsonb(OLD) - ARRAY['created_at', 'updated_at'])
        AND EXISTS (
            SELECT FROM public.market_analysis_signal_lake link
            JOIN public.analytical_lake lake ON lake.event_id = link.event_id
            WHERE link.signal_id = NEW.signal_id
                AND lake.event_type = 'SIGNAL_LIFECYCLE'
        ) THEN
        RETURN NEW;
    END IF;
    SELECT entity_id INTO v_entity_id FROM public.instruments
    WHERE instrument_id = NEW.instrument_id;
    IF v_assessment_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT FROM public.analytical_lake
            WHERE event_id = v_assessment_id AND event_type = 'SIGNAL_ASSESSMENT'
                AND subjects @> jsonb_build_array(jsonb_build_object(
                    'instrument_id', NEW.instrument_id::text))) THEN
            RAISE EXCEPTION 'Signal requires its exact instrument assessment';
        END IF;
        INSERT INTO public.market_analysis_signal_lake VALUES (NEW.signal_id, v_assessment_id)
        ON CONFLICT DO NOTHING;
    END IF;
    v_event_id := public.sp_save_analytical_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', 'SIGNAL_LIFECYCLE', 'stage', 'VALIDATED', 'source', 'MIP',
        'event_key', NEW.signal_id::text,
        'occurred_at', NEW.observed_at, 'observed_at', clock_timestamp(),
        'subjects', jsonb_build_array(jsonb_build_object(
            'instrument_id', NEW.instrument_id::text, 'entity_id', v_entity_id::text)),
        'parent_event_ids', CASE WHEN v_assessment_id IS NULL THEN '[]'::jsonb
            ELSE jsonb_build_array(v_assessment_id) END,
        'payload', jsonb_build_object('signal_id', NEW.signal_id::text,
            'signal', v_snapshot, 'legacy_without_assessment', v_assessment_id IS NULL),
        'evidence', '{}'::jsonb, 'provenance', '{}'::jsonb
    ));
    INSERT INTO public.market_analysis_signal_lake VALUES (NEW.signal_id, v_event_id)
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_market_analysis_capture_lake
AFTER INSERT OR UPDATE ON public.trading_signals
FOR EACH ROW EXECUTE FUNCTION public.trg_market_analysis_capture_lake();

CREATE FUNCTION public.sp_market_analysis_apply_lake_enrichment(
    p_signal_id uuid, p_enrichment_fingerprint varchar, p_llm_score numeric,
    p_llm_analysis jsonb, p_composite_score numeric DEFAULT NULL,
    p_assessment_event_id uuid DEFAULT NULL, p_input_signal jsonb DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    v_signal public.trading_signals%ROWTYPE;
    v_entity_id uuid;
    v_event_id uuid;
BEGIN
    SELECT * INTO v_signal FROM public.trading_signals
    WHERE signal_id = p_signal_id FOR UPDATE;
    IF NOT FOUND OR v_signal.status NOT IN ('CANDIDATE', 'CONFIRMED')
        OR (v_signal.evidence->>'assessment_event_id')::uuid
            IS DISTINCT FROM p_assessment_event_id THEN
        RETURN false;
    END IF;
    IF p_input_signal IS NULL OR jsonb_typeof(p_input_signal) <> 'object'
        OR p_input_signal->>'signal_id' IS DISTINCT FROM p_signal_id::text THEN
        RAISE EXCEPTION 'Enrichment requires the exact input sent to the model';
    END IF;
    IF NOT public.sp_market_analysis_apply_llm_enrichment(p_signal_id,
        p_enrichment_fingerprint, p_llm_score, p_llm_analysis, p_composite_score) THEN
        RETURN false;
    END IF;
    SELECT entity_id INTO v_entity_id FROM public.instruments
    WHERE instrument_id = v_signal.instrument_id;
    v_event_id := public.sp_save_analytical_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', 'SIGNAL_ENRICHMENT', 'stage', 'ENRICHED', 'source', 'MIP',
        'event_key', jsonb_build_array(p_signal_id, p_assessment_event_id,
            p_enrichment_fingerprint)::text,
        'occurred_at', v_signal.observed_at, 'observed_at', clock_timestamp(),
        'subjects', jsonb_build_array(jsonb_build_object(
            'instrument_id', v_signal.instrument_id::text, 'entity_id', v_entity_id::text)),
        'parent_event_ids', CASE WHEN p_assessment_event_id IS NULL THEN '[]'::jsonb
            ELSE jsonb_build_array(p_assessment_event_id) END,
        'payload', jsonb_build_object('signal_id', p_signal_id::text,
            'enrichment_fingerprint', p_enrichment_fingerprint,
            'llm_score', p_llm_score, 'composite_score', p_composite_score,
            'llm_analysis', p_llm_analysis),
        'evidence', jsonb_build_object('input_signal', p_input_signal),
        'provenance', '{}'::jsonb
    ));
    INSERT INTO public.market_analysis_signal_lake VALUES (p_signal_id, v_event_id)
    ON CONFLICT DO NOTHING;
    RETURN true;
END;
$$;

CREATE FUNCTION public.sp_cleanup_analytical_lake(
    p_threshold_days integer, p_dry_run boolean DEFAULT true
) RETURNS TABLE(cutoff_at timestamptz, eligible_rows bigint,
    protected_rows bigint, deleted_rows bigint)
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
SET lock_timeout = '10s' AS $$
DECLARE
    v_setting text;
BEGIN
    IF p_threshold_days IS NULL OR p_threshold_days <= 0 OR p_dry_run IS NULL THEN
        RAISE EXCEPTION 'Specify positive retention days and a non-null dry-run flag';
    END IF;
    LOCK TABLE public.trading_signals IN SHARE MODE;
    LOCK TABLE public.event_lake IN ROW EXCLUSIVE MODE;
    LOCK TABLE public.analytical_lake IN SHARE ROW EXCLUSIVE MODE;
    cutoff_at := statement_timestamp() - make_interval(days => p_threshold_days);
    deleted_rows := 0;
    CREATE TEMP TABLE analytical_lake_cleanup_protected (
        event_id uuid PRIMARY KEY
    ) ON COMMIT DROP;
    WITH RECURSIVE edges AS (
        SELECT event_id, parent_id FROM public.analytical_lake
        CROSS JOIN LATERAL unnest(parent_event_ids) parents(parent_id)
        UNION
        SELECT event_id, supersedes_event_id FROM public.analytical_lake
        WHERE supersedes_event_id IS NOT NULL
        UNION
        SELECT supersedes_event_id, event_id FROM public.analytical_lake
        WHERE supersedes_event_id IS NOT NULL
    ), retained(event_id) AS (
        SELECT event_id FROM public.analytical_lake WHERE last_seen_at >= cutoff_at
            OR occurred_at >= statement_timestamp()
            OR event_day >= (statement_timestamp() AT TIME ZONE 'UTC')::date
        UNION
        SELECT event_id FROM public.market_analysis_signal_lake
        UNION
        SELECT edges.parent_id FROM retained JOIN edges USING (event_id)
    )
    INSERT INTO pg_temp.analytical_lake_cleanup_protected
    SELECT retained.event_id FROM retained
    JOIN public.analytical_lake lake USING (event_id);
    CREATE TEMP TABLE analytical_lake_cleanup_candidates (
        event_id uuid PRIMARY KEY
    ) ON COMMIT DROP;
    INSERT INTO pg_temp.analytical_lake_cleanup_candidates
    SELECT event_id FROM public.analytical_lake lake WHERE NOT EXISTS (
        SELECT FROM pg_temp.analytical_lake_cleanup_protected retained
        WHERE retained.event_id = lake.event_id
    );
    SELECT count(*) INTO eligible_rows FROM pg_temp.analytical_lake_cleanup_candidates;
    SELECT count(*) INTO protected_rows FROM public.analytical_lake lake
    JOIN pg_temp.analytical_lake_cleanup_protected USING (event_id)
    WHERE lake.last_seen_at < cutoff_at;
    IF NOT p_dry_run THEN
        v_setting := current_setting('mip.analytical_lake_cleanup', true);
        PERFORM set_config('mip.analytical_lake_cleanup', 'on', true);
        DELETE FROM public.analytical_lake lake
        USING pg_temp.analytical_lake_cleanup_candidates candidate
        WHERE lake.event_id = candidate.event_id;
        GET DIAGNOSTICS deleted_rows = ROW_COUNT;
        PERFORM set_config('mip.analytical_lake_cleanup', coalesce(v_setting, ''), true);
    END IF;
    DROP TABLE pg_temp.analytical_lake_cleanup_candidates;
    DROP TABLE pg_temp.analytical_lake_cleanup_protected;
    RETURN NEXT;
END;
$$;

UPDATE public.trading_signals SET evidence = evidence;

COMMIT;