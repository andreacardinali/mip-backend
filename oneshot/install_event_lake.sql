ROLLBACK;

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
    ('NEWS_ASSOCIATION', 1, ARRAY['ACQUIRED'],
        '{"news_entity_id":"string","news_id":"string","entity_id":"string"}'),
    ('NEWS_CLUSTER', 1, ARRAY['ACQUIRED'], '{"cluster_id":"string"}'),
    ('NEWS_IMPACT', 1, ARRAY['ENRICHED'], '{"impact_id":"string"}'),
    ('CATALYST_HISTORY', 1, ARRAY['ACQUIRED'], '{"id":"string"}'),
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
    entity_id uuid REFERENCES public.entities(entity_id),
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
    UNIQUE NULLS NOT DISTINCT (event_type, stage, source, event_key, entity_id, revision),
    CHECK ((revision = 1) = (supersedes_event_id IS NULL))
);

DO $$
DECLARE
    v_entity_exists boolean;
    v_has_entity_unique boolean := false;
    v_old_constraints text[] := '{}';
    v_constraint record;
    v_constraint_name text;
BEGIN
    LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE;
    SELECT EXISTS (
        SELECT FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.event_lake'::regclass
          AND attname = 'entity_id' AND NOT attisdropped
    ) INTO v_entity_exists;
    FOR v_constraint IN
        SELECT constraint_row.conname, index_row.indnullsnotdistinct,
               ARRAY(
                   SELECT attribute.attname::text
                   FROM unnest(constraint_row.conkey) WITH ORDINALITY
                       AS key_column(attnum, position)
                   JOIN pg_catalog.pg_attribute attribute
                     ON attribute.attrelid = constraint_row.conrelid
                    AND attribute.attnum = key_column.attnum
                   ORDER BY key_column.position
               ) AS column_names
        FROM pg_catalog.pg_constraint constraint_row
                JOIN pg_catalog.pg_index index_row
                    ON index_row.indexrelid = constraint_row.conindid
        WHERE constraint_row.conrelid = 'public.event_lake'::regclass
          AND constraint_row.contype = 'u'
    LOOP
        IF v_constraint.column_names =
            ARRAY['event_type', 'stage', 'source', 'event_key', 'revision'] THEN
            v_old_constraints := array_append(v_old_constraints, v_constraint.conname::text);
        ELSIF v_constraint.column_names =
            ARRAY['event_type', 'stage', 'source', 'event_key', 'entity_id', 'revision']
            AND v_constraint.indnullsnotdistinct THEN
            v_has_entity_unique := true;
        END IF;
    END LOOP;
    IF NOT v_entity_exists OR cardinality(v_old_constraints) > 0
       OR NOT v_has_entity_unique THEN
        IF EXISTS (SELECT FROM public.event_lake) THEN
            RAISE EXCEPTION 'Existing event_lake requires an entity-scoped data migration'
                USING HINT = 'Do not run cutover or remove events. Migrate existing subjects and revision chains before reinstalling.';
        END IF;
        IF NOT v_entity_exists THEN
            ALTER TABLE public.event_lake
                ADD COLUMN entity_id uuid REFERENCES public.entities(entity_id);
        END IF;
        FOREACH v_constraint_name IN ARRAY v_old_constraints LOOP
            EXECUTE format('ALTER TABLE public.event_lake DROP CONSTRAINT %I',
                           v_constraint_name);
        END LOOP;
        IF NOT v_has_entity_unique THEN
            ALTER TABLE public.event_lake
                ADD CONSTRAINT event_lake_entity_revision_key
                UNIQUE NULLS NOT DISTINCT
                (event_type, stage, source, event_key, entity_id, revision);
        END IF;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS ix_event_lake_entity
    ON public.event_lake(entity_id, event_type, stage, occurred_at DESC)
    WHERE entity_id IS NOT NULL;
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

CREATE OR REPLACE FUNCTION public.sp_event_resolve_catalyst_entity(
    p_entity_id uuid, p_symbol text
)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_symbol text := upper(nullif(btrim(p_symbol), ''));
    v_matches uuid[];
BEGIN
    IF p_entity_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT FROM public.entities
                       WHERE entity_id = p_entity_id AND active) THEN
            RAISE EXCEPTION 'Active entity not found: %', p_entity_id;
        END IF;
        IF v_symbol IS NOT NULL AND NOT EXISTS (
            SELECT FROM public.instruments
            WHERE entity_id = p_entity_id AND upper(btrim(symbol)) = v_symbol
        ) THEN
            RAISE EXCEPTION 'Symbol % does not belong to entity %', v_symbol, p_entity_id;
        END IF;
        RETURN p_entity_id;
    END IF;
    SELECT array_agg(DISTINCT instrument.entity_id ORDER BY instrument.entity_id)
    INTO v_matches
    FROM public.instruments instrument
    JOIN public.entities entity USING (entity_id)
    WHERE entity.active AND upper(btrim(instrument.symbol)) = v_symbol;
    IF coalesce(cardinality(v_matches), 0) <> 1 THEN
        RAISE EXCEPTION 'Symbol % must resolve to exactly one active entity: %',
            v_symbol, v_matches;
    END IF;
    RETURN v_matches[1];
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_event_resolve_filing_entity(p_symbol text, p_cik text)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_symbol text := nullif(upper(btrim(p_symbol)), '');
    v_cik text := nullif(regexp_replace(coalesce(p_cik, ''), '[^0-9]', '', 'g'), '');
    v_type_id uuid;
    v_identity_id uuid;
    v_entity_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'filing-issuer:' || coalesce(v_cik, v_symbol, 'UNKNOWN'), 0
    ));
    SELECT identity_type_id INTO v_type_id FROM public.identity_types
    WHERE upper(code) = 'CIK' AND upper(target_kind) = 'ENTITY'
    ORDER BY identity_type_id LIMIT 1;
    IF v_cik IS NOT NULL AND v_type_id IS NOT NULL THEN
        SELECT link.entity_id, identity_row.identity_id
        INTO v_entity_id, v_identity_id
        FROM public.identities identity_row
        JOIN public.entity_identities link USING (identity_id)
        WHERE identity_row.identity_type_id = v_type_id
          AND identity_row.normalized_value = v_cik
        ORDER BY link.is_primary DESC, link.verified_at DESC NULLS LAST,
                 link.created_at DESC LIMIT 1;
    END IF;
    IF v_entity_id IS NULL AND v_symbol IS NOT NULL THEN
        SELECT entity_id INTO v_entity_id FROM public.instruments
        WHERE upper(btrim(symbol)) = v_symbol
        ORDER BY (exchange IS NULL), updated_at DESC NULLS LAST,
                 created_at DESC NULLS LAST, instrument_id LIMIT 1;
    END IF;
    IF v_entity_id IS NULL THEN
        INSERT INTO public.entities(name, entity_type)
        VALUES (coalesce(v_symbol, v_cik, 'UNKNOWN SEC ISSUER'), 'COMPANY')
        RETURNING entity_id INTO v_entity_id;
    END IF;
    IF v_type_id IS NOT NULL AND v_cik IS NOT NULL AND v_identity_id IS NULL THEN
        SELECT identity_id INTO v_identity_id FROM public.identities
        WHERE identity_type_id = v_type_id AND normalized_value = v_cik LIMIT 1;
        IF v_identity_id IS NULL THEN
            INSERT INTO public.identities(identity_type_id, identity_value, normalized_value)
            VALUES (v_type_id, coalesce(nullif(btrim(p_cik), ''), v_cik), v_cik)
            RETURNING identity_id INTO v_identity_id;
        END IF;
        IF NOT EXISTS (SELECT FROM public.entity_identities
                       WHERE entity_id = v_entity_id AND identity_id = v_identity_id) THEN
            INSERT INTO public.entity_identities(entity_id, identity_id, is_primary, verified_at)
            VALUES (v_entity_id, v_identity_id, NOT EXISTS (
                SELECT FROM public.entity_identities WHERE entity_id = v_entity_id
            ), now());
        END IF;
    END IF;
    RETURN v_entity_id;
END;
$$;

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
    v_subject jsonb;
    v_entity_id uuid;
    v_entity_ids uuid[];
    v_resolved_subjects jsonb := '[]'::jsonb;
    v_child_event_id uuid;
    v_payload jsonb := p_envelope->'payload';
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
    IF v_event_type IN ('CATALYST', 'FILING')
       AND p_envelope->'provenance'->>'legacy_table' IS NULL THEN
        FOR v_subject IN
            SELECT value FROM jsonb_array_elements(
                CASE WHEN jsonb_array_length(p_envelope->'subjects') = 0
                     THEN '[{}]'::jsonb ELSE p_envelope->'subjects' END
            )
        LOOP
            IF v_event_type = 'CATALYST' THEN
                v_entity_id := public.sp_event_resolve_catalyst_entity(
                    (v_subject->>'entity_id')::uuid, v_subject->>'symbol'
                );
            ELSE
                v_entity_id := public.sp_event_resolve_filing_entity(
                    v_subject->>'symbol', p_envelope->'payload'->>'cik'
                );
            END IF;
            v_resolved_subjects := v_resolved_subjects || jsonb_build_array(
                v_subject || jsonb_build_object('entity_id', v_entity_id)
            );
        END LOOP;
        p_envelope := jsonb_set(p_envelope, '{subjects}', v_resolved_subjects);
    END IF;
    SELECT array_agg(DISTINCT (value->>'entity_id')::uuid
                     ORDER BY (value->>'entity_id')::uuid)
    INTO v_entity_ids
    FROM jsonb_array_elements(p_envelope->'subjects')
    WHERE value->>'entity_id' IS NOT NULL;
    IF cardinality(v_entity_ids) > 1 THEN
        FOREACH v_entity_id IN ARRAY v_entity_ids LOOP
            SELECT jsonb_agg(value ORDER BY value) INTO v_subjects
            FROM jsonb_array_elements(p_envelope->'subjects')
            WHERE value->>'entity_id' IS NULL
               OR (value->>'entity_id')::uuid = v_entity_id;
            v_child_event_id := public.sp_save_event(
                jsonb_set(p_envelope, '{subjects}', v_subjects)
            );
            v_event_id := coalesce(v_event_id, v_child_event_id);
        END LOOP;
        RETURN v_event_id;
    END IF;
    v_entity_id := v_entity_ids[1];
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
    IF v_event_type IN ('CATALYST', 'FILING')
       AND p_envelope->'provenance'->>'legacy_table' IS NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            jsonb_build_array(v_event_type, v_stage, v_source)::text, 0
        ));
        SELECT * INTO v_previous FROM public.event_lake
        WHERE event_type = v_event_type AND stage = v_stage AND source = v_source
                    AND entity_id IS NOT DISTINCT FROM v_entity_id
          AND (event_key = v_key OR (
            v_event_type = 'FILING'
            AND payload->>'cik' = v_payload->>'cik'
            AND CASE WHEN nullif(p_envelope->>'source_event_id', '') IS NOT NULL
                THEN source_event_id = p_envelope->>'source_event_id'
                ELSE payload->>'filing_type' = v_payload->>'filing_type'
                     AND occurred_at = v_occurred_at END
          ) OR (
            v_event_type = 'CATALYST'
            AND upper(payload->>'subtype') = upper(v_payload->>'subtype')
            AND CASE WHEN nullif(p_envelope->>'source_event_id', '') IS NOT NULL
                THEN source_event_id = p_envelope->>'source_event_id'
                WHEN p_envelope->'raw_payload'->>'fiscalYear' IS NOT NULL
                     AND coalesce(p_envelope->'raw_payload'->>'fiscalQuarter',
                                  p_envelope->'raw_payload'->>'fiscalPeriod') IS NOT NULL
                THEN raw_payload->>'fiscalYear' = p_envelope->'raw_payload'->>'fiscalYear'
                     AND coalesce(raw_payload->>'fiscalQuarter', raw_payload->>'fiscalPeriod') =
                         coalesce(p_envelope->'raw_payload'->>'fiscalQuarter',
                                  p_envelope->'raw_payload'->>'fiscalPeriod')
                ELSE source_event_id IS NULL AND event_day = v_event_day END
          ))
        ORDER BY (event_key = v_key) DESC, created_at DESC, revision DESC, event_id LIMIT 1;
        IF FOUND THEN
            v_key := v_previous.event_key;
            IF v_event_type = 'CATALYST' AND v_previous.payload ? 'catalyst_id' THEN
                v_payload := v_payload || jsonb_build_object(
                    'catalyst_id', v_previous.payload->'catalyst_id'
                );
            ELSIF v_event_type = 'FILING' AND v_previous.payload ? 'filing_id' THEN
                v_payload := v_payload || jsonb_build_object(
                    'filing_id', v_previous.payload->'filing_id',
                    'created_at', v_previous.payload->'created_at'
                );
            END IF;
        END IF;
        p_envelope := jsonb_set(p_envelope, '{payload}', v_payload);
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
        jsonb_build_array(v_event_type, v_stage, v_source, v_key, v_entity_id)::text, 0
    ));
    SELECT * INTO v_previous
    FROM public.event_lake
    WHERE event_type = v_event_type AND stage = v_stage
        AND source = v_source AND event_key = v_key
        AND entity_id IS NOT DISTINCT FROM v_entity_id
    ORDER BY revision DESC
    LIMIT 1;
    IF FOUND AND v_previous.content_hash = v_hash THEN
        UPDATE public.event_lake
        SET last_seen_at = clock_timestamp()
        WHERE event_id = v_previous.event_id;
        RETURN v_previous.event_id;
    END IF;
    INSERT INTO public.event_lake (
        entity_id, schema_version, event_type, payload_version, stage, source, source_event_id,
        event_key, revision, supersedes_event_id, content_hash,
        occurred_at, event_day, observed_at, subjects, parent_event_ids,
        payload, raw_payload, evidence, provenance
    )
    VALUES (
        v_entity_id, 1, v_event_type, v_payload_version, v_stage, v_source,
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