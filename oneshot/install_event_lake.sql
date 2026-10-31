BEGIN;

CREATE TABLE IF NOT EXISTS public.event_type_catalog (
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    payload_version integer NOT NULL CHECK (payload_version > 0),
    allowed_stages text[] NOT NULL CHECK (cardinality(allowed_stages) > 0),
    required_payload_types jsonb NOT NULL
        CHECK (jsonb_typeof(required_payload_types) = 'object'),
    PRIMARY KEY (event_type, payload_version)
);

INSERT INTO public.event_type_catalog (
    event_type, payload_version, allowed_stages, required_payload_types
)
VALUES
    ('NEWS_ARTICLE', 1, ARRAY['ACQUIRED'], '{"headline":"string"}'),
    ('NEWS_SCORE', 1, ARRAY['ENRICHED'],
        '{"news_entity_id":"string","relevance":"number","sentiment":"number","impact":"number"}'),
    ('CATALYST', 1, ARRAY['ACQUIRED'],
        '{"subtype":"string","status":"string","importance":"string","title":"string","details":"object"}'),
    ('FILING', 1, ARRAY['ACQUIRED'],
        '{"filing_type":"string","cik":"string","accepted_at":"string"}'),
    ('DAILY_MOVER', 1, ARRAY['VALIDATED'], '{"dailyChangePct":"number"}'),
    ('UPCOMING_CATALYST', 1, ARRAY['VALIDATED'],
        '{"eventType":"string","eventDate":"string","eventConfidence":"number"}'),
    ('BREAKING_CATALYST', 1, ARRAY['VALIDATED'],
        '{"eventType":"string","eventDate":"string","eventConfidence":"number"}')
ON CONFLICT (event_type, payload_version) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.event_lake (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    event_type text NOT NULL,
    payload_version integer NOT NULL,
    stage text NOT NULL CHECK (stage IN ('ACQUIRED', 'ENRICHED', 'VALIDATED')),
    source text NOT NULL CHECK (btrim(source) <> ''),
    source_event_id text,
    event_key text NOT NULL CHECK (btrim(event_key) <> ''),
    revision integer NOT NULL CHECK (revision > 0),
    supersedes_event_id uuid REFERENCES public.event_lake(event_id),
    content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    occurred_at timestamptz,
    event_day date,
    observed_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL,
    subjects jsonb NOT NULL CHECK (jsonb_typeof(subjects) = 'array'),
    parent_event_ids uuid[] NOT NULL DEFAULT '{}',
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    raw_payload jsonb CHECK (jsonb_typeof(raw_payload) = 'object'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    FOREIGN KEY (event_type, payload_version)
        REFERENCES public.event_type_catalog(event_type, payload_version),
    UNIQUE (event_type, stage, source, event_key, revision),
    CHECK ((revision = 1) = (supersedes_event_id IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_event_lake_event_day
    ON public.event_lake(event_type, event_day);
CREATE INDEX IF NOT EXISTS ix_event_lake_ingested_at
    ON public.event_lake(ingested_at);
CREATE INDEX IF NOT EXISTS ix_event_lake_subjects
    ON public.event_lake USING gin(subjects);
CREATE INDEX IF NOT EXISTS ix_event_lake_parent_event_ids
    ON public.event_lake USING gin(parent_event_ids);

CREATE OR REPLACE FUNCTION public.event_lake_timestamps()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Event lake revisions cannot be deleted';
    ELSIF TG_OP = 'INSERT' THEN
        NEW.created_at := v_now;
        NEW.updated_at := v_now;
        NEW.ingested_at := v_now;
        NEW.last_seen_at := v_now;
    ELSE
        IF (to_jsonb(NEW) - ARRAY['last_seen_at', 'updated_at'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['last_seen_at', 'updated_at']) THEN
            RAISE EXCEPTION 'Event lake content is immutable; insert a revision';
        END IF;
        NEW.last_seen_at := greatest(OLD.last_seen_at, NEW.last_seen_at, v_now);
        NEW.updated_at := v_now;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_event_lake_timestamps ON public.event_lake;
CREATE TRIGGER trg_event_lake_timestamps
BEFORE INSERT OR UPDATE OR DELETE ON public.event_lake
FOR EACH ROW EXECUTE FUNCTION public.event_lake_timestamps();

CREATE OR REPLACE FUNCTION public.sp_save_event(p_envelope jsonb)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = pg_catalog, public
SET timezone = 'UTC'
SET datestyle = 'ISO, YMD'
AS $$
DECLARE
    v_type public.event_type_catalog%ROWTYPE;
    v_previous public.event_lake%ROWTYPE;
    v_event_id uuid;
    v_field record;
    v_event_type text := p_envelope->>'event_type';
    v_stage text := p_envelope->>'stage';
    v_source text := p_envelope->>'source';
    v_key text := p_envelope->>'event_key';
    v_payload_version integer := (p_envelope->>'payload_version')::integer;
    v_occurred_at timestamptz := (p_envelope->>'occurred_at')::timestamptz;
    v_event_day date := (p_envelope->>'event_day')::date;
    v_observed_at timestamptz := coalesce(
        (p_envelope->>'observed_at')::timestamptz, clock_timestamp()
    );
    v_parents uuid[];
    v_subjects jsonb;
    v_semantic jsonb;
    v_hash text;
BEGIN
    IF jsonb_typeof(p_envelope) IS DISTINCT FROM 'object'
        OR (p_envelope->>'schema_version')::integer IS DISTINCT FROM 1
        OR nullif(btrim(v_source), '') IS NULL
        OR nullif(btrim(v_key), '') IS NULL
        OR v_stage IS NULL
        OR v_stage NOT IN ('ACQUIRED', 'ENRICHED', 'VALIDATED') THEN
        RAISE EXCEPTION 'Invalid event envelope';
    END IF;
    SELECT * INTO v_type
    FROM public.event_type_catalog
    WHERE event_type = v_event_type AND payload_version = v_payload_version;
    IF NOT FOUND OR NOT v_stage = ANY(v_type.allowed_stages) THEN
        RAISE EXCEPTION 'Unregistered event type/version or stage: %/%/%',
            v_event_type, v_payload_version, v_stage;
    END IF;
    FOREACH v_source IN ARRAY ARRAY['payload', 'evidence', 'provenance'] LOOP
        IF jsonb_typeof(p_envelope->v_source) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION '% must be an object', v_source;
        END IF;
    END LOOP;
    v_source := p_envelope->>'source';
    IF jsonb_typeof(p_envelope->'subjects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_envelope->'parent_event_ids') IS DISTINCT FROM 'array'
        OR (p_envelope->'raw_payload' <> 'null'::jsonb
            AND jsonb_typeof(p_envelope->'raw_payload') <> 'object') THEN
        RAISE EXCEPTION 'Invalid subjects, parents or raw payload';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements(p_envelope->'subjects') AS subject(value)
        WHERE jsonb_typeof(value) <> 'object'
    ) THEN
        RAISE EXCEPTION 'Subjects must be objects';
    END IF;
    FOR v_field IN SELECT * FROM jsonb_each_text(v_type.required_payload_types) LOOP
        IF jsonb_typeof(p_envelope->'payload'->v_field.key)
            IS DISTINCT FROM v_field.value THEN
            RAISE EXCEPTION 'Invalid % payload field %: expected %',
                v_event_type, v_field.key, v_field.value;
        END IF;
    END LOOP;
    IF (p_envelope->>'occurred_at') IS NOT NULL
        AND (p_envelope->>'occurred_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
        OR (p_envelope->>'observed_at') IS NOT NULL
        AND (p_envelope->>'observed_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
        RAISE EXCEPTION 'Instants require an explicit UTC offset';
    END IF;
    SELECT coalesce(jsonb_agg(value ORDER BY value), '[]'::jsonb)
    INTO v_subjects
    FROM (
        SELECT DISTINCT value FROM jsonb_array_elements(p_envelope->'subjects')
    ) AS subjects;
    SELECT coalesce(array_agg(DISTINCT value::uuid ORDER BY value::uuid), '{}'::uuid[])
    INTO v_parents
    FROM jsonb_array_elements_text(p_envelope->'parent_event_ids');
    IF EXISTS (
        SELECT FROM unnest(v_parents) AS parent(event_id)
        WHERE NOT EXISTS (
            SELECT FROM public.event_lake AS lake WHERE lake.event_id = parent.event_id
        )
    ) THEN
        RAISE EXCEPTION 'Unknown parent event';
    END IF;
    v_semantic := jsonb_build_object(
        'schema_version', 1, 'payload_version', v_payload_version,
        'source_event_id', p_envelope->>'source_event_id',
        'occurred_at', v_occurred_at, 'event_day', v_event_day,
        'subjects', v_subjects, 'parent_event_ids', v_parents,
        'payload', p_envelope->'payload', 'evidence', p_envelope->'evidence'
    );
    v_hash := encode(sha256(convert_to(v_semantic::text, 'UTF8')), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(
        jsonb_build_array(v_event_type, v_stage, v_source, v_key)::text, 0
    ));
    SELECT * INTO v_previous
    FROM public.event_lake
    WHERE event_type = v_event_type AND stage = v_stage
        AND source = v_source AND event_key = v_key
    ORDER BY revision DESC
    LIMIT 1;
    IF FOUND AND v_previous.content_hash = v_hash THEN
        UPDATE public.event_lake
        SET last_seen_at = clock_timestamp()
        WHERE event_id = v_previous.event_id;
        RETURN v_previous.event_id;
    END IF;
    INSERT INTO public.event_lake (
        schema_version, event_type, payload_version, stage, source, source_event_id,
        event_key, revision, supersedes_event_id, content_hash,
        occurred_at, event_day, observed_at, subjects, parent_event_ids,
        payload, raw_payload, evidence, provenance
    )
    VALUES (
        1, v_event_type, v_payload_version, v_stage, v_source,
        p_envelope->>'source_event_id', v_key,
        coalesce(v_previous.revision, 0) + 1, v_previous.event_id, v_hash,
        v_occurred_at, v_event_day, v_observed_at, v_subjects, v_parents,
        p_envelope->'payload', nullif(p_envelope->'raw_payload', 'null'::jsonb),
        p_envelope->'evidence', p_envelope->'provenance'
    )
    RETURNING event_id INTO v_event_id;
    RETURN v_event_id;
END;
$$;

COMMIT;