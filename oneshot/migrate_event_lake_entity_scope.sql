BEGIN;

SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.entities IN SHARE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.event_lake'::regclass
          AND attname = 'entity_id' AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'Migration requires the pre-entity event_lake schema';
    END IF;
    IF EXISTS (
        SELECT FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.event_lake'::regclass AND NOT tgisinternal
          AND (tgname <> 'trg_event_lake_timestamps' OR tgenabled <> 'O')
    ) OR NOT EXISTS (
        SELECT FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.event_lake'::regclass
          AND tgname = 'trg_event_lake_timestamps' AND tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION 'Unexpected event_lake triggers; review before migration';
    END IF;
    IF EXISTS (
        SELECT FROM public.event_lake original
        CROSS JOIN LATERAL jsonb_array_elements(original.subjects) subject(value)
        WHERE jsonb_typeof(subject.value) IS DISTINCT FROM 'object'
           OR (subject.value->>'entity_id' IS NOT NULL AND (
               jsonb_typeof(subject.value->'entity_id') IS DISTINCT FROM 'string'
               OR subject.value->>'entity_id' !~*
                   '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
           ))
    ) THEN
        RAISE EXCEPTION 'Invalid entity UUID or subject object in event_lake';
    END IF;
    IF EXISTS (
        SELECT FROM public.event_lake original
        CROSS JOIN LATERAL jsonb_array_elements(original.subjects) subject(value)
        WHERE subject.value->>'entity_id' IS NOT NULL
          AND NOT EXISTS (
              SELECT FROM public.entities entity
              WHERE entity.entity_id = (subject.value->>'entity_id')::uuid
          )
    ) THEN
        RAISE EXCEPTION 'Subject references an entity missing from public.entities';
    END IF;
    IF EXISTS (
        SELECT FROM (
            SELECT original.*, row_number() OVER (
                PARTITION BY event_type, stage, source, event_key ORDER BY revision
            ) AS expected_revision,
            lag(event_id) OVER (
                PARTITION BY event_type, stage, source, event_key ORDER BY revision
            ) AS expected_previous
            FROM public.event_lake original
        ) chain
        WHERE revision <> expected_revision
           OR supersedes_event_id IS DISTINCT FROM expected_previous
    ) THEN
        RAISE EXCEPTION 'Existing revision chains are inconsistent';
    END IF;
    IF EXISTS (
        SELECT FROM public.event_lake original
        CROSS JOIN LATERAL unnest(original.parent_event_ids) parent(event_id)
        WHERE parent.event_id IS NULL OR NOT EXISTS (
            SELECT FROM public.event_lake referenced
            WHERE referenced.event_id = parent.event_id
        )
    ) THEN
        RAISE EXCEPTION 'Existing event has an unknown or null parent UUID';
    END IF;
END;
$$;

CREATE SCHEMA event_lake_entity_migration;
CREATE TABLE event_lake_entity_migration.original AS TABLE public.event_lake;
ALTER TABLE event_lake_entity_migration.original ADD PRIMARY KEY (event_id);

CREATE TABLE event_lake_entity_migration.event_map (
    original_event_id uuid NOT NULL
        REFERENCES event_lake_entity_migration.original(event_id),
    entity_id uuid,
    migrated_event_id uuid PRIMARY KEY,
    UNIQUE NULLS NOT DISTINCT (original_event_id, entity_id)
);

INSERT INTO event_lake_entity_migration.event_map (
    original_event_id, entity_id, migrated_event_id
)
SELECT event_id, entity_id,
       CASE WHEN entity_position = 1 THEN event_id ELSE gen_random_uuid() END
FROM (
    SELECT original.event_id, resolved.entity_id,
           row_number() OVER (
               PARTITION BY original.event_id ORDER BY resolved.entity_id NULLS FIRST
           ) AS entity_position
    FROM event_lake_entity_migration.original original
    LEFT JOIN LATERAL (
        SELECT DISTINCT (subject.value->>'entity_id')::uuid AS entity_id
        FROM jsonb_array_elements(original.subjects) subject(value)
        WHERE subject.value->>'entity_id' IS NOT NULL
    ) resolved ON true
) expanded;

DO $$
BEGIN
    IF EXISTS (
        SELECT FROM event_lake_entity_migration.event_map
        GROUP BY original_event_id HAVING count(*) > 1
    ) AND EXISTS (
        SELECT FROM pg_catalog.pg_constraint
        WHERE contype = 'f' AND confrelid = 'public.event_lake'::regclass
          AND conrelid <> 'public.event_lake'::regclass
    ) THEN
        RAISE EXCEPTION 'External foreign keys require a reviewed multi-entity mapping';
    END IF;
END;
$$;

CREATE TEMP TABLE event_lake_entity_plan ON COMMIT DROP AS
SELECT original.*, mapping.entity_id, mapping.migrated_event_id,
       row_number() OVER (
           PARTITION BY original.event_type, original.stage, original.source,
                        original.event_key, mapping.entity_id
           ORDER BY original.revision
       )::integer AS migrated_revision,
       lag(mapping.migrated_event_id) OVER (
           PARTITION BY original.event_type, original.stage, original.source,
                        original.event_key, mapping.entity_id
           ORDER BY original.revision
       ) AS migrated_previous,
       (
           SELECT coalesce(jsonb_agg(subject.value ORDER BY subject.value), '[]'::jsonb)
           FROM (
               SELECT DISTINCT value
               FROM jsonb_array_elements(original.subjects)
               WHERE value->>'entity_id' IS NULL
                  OR (value->>'entity_id')::uuid = mapping.entity_id
           ) subject
       ) AS migrated_subjects,
       (
           SELECT coalesce(array_agg(DISTINCT candidate.migrated_event_id
                                     ORDER BY candidate.migrated_event_id), '{}'::uuid[])
           FROM unnest(original.parent_event_ids) parent(event_id)
           JOIN event_lake_entity_migration.event_map candidate
             ON candidate.original_event_id = parent.event_id
           WHERE candidate.entity_id IS NOT DISTINCT FROM mapping.entity_id
              OR NOT EXISTS (
                  SELECT FROM event_lake_entity_migration.event_map same_entity
                  WHERE same_entity.original_event_id = parent.event_id
                    AND same_entity.entity_id IS NOT DISTINCT FROM mapping.entity_id
              )
       ) AS migrated_parents
FROM event_lake_entity_migration.original original
JOIN event_lake_entity_migration.event_map mapping
  ON mapping.original_event_id = original.event_id;

ALTER TABLE public.event_lake DISABLE TRIGGER trg_event_lake_timestamps;
ALTER TABLE public.event_lake
    ADD COLUMN entity_id uuid REFERENCES public.entities(entity_id);

CREATE TEMP TABLE event_lake_entity_foreign_keys ON COMMIT DROP AS
SELECT conname, pg_catalog.pg_get_constraintdef(oid) AS definition
FROM pg_catalog.pg_constraint
WHERE conrelid = 'public.event_lake'::regclass
  AND confrelid = 'public.event_lake'::regclass AND contype = 'f';

DO $$
DECLARE
    constraint_record record;
BEGIN
    FOR constraint_record IN SELECT * FROM event_lake_entity_foreign_keys LOOP
        EXECUTE format('ALTER TABLE public.event_lake DROP CONSTRAINT %I',
                       constraint_record.conname);
    END LOOP;
END;
$$;

DO $$
DECLARE
    constraint_record record;
    removed_constraints integer := 0;
BEGIN
    FOR constraint_record IN
        SELECT constraint_row.conname,
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
        WHERE constraint_row.conrelid = 'public.event_lake'::regclass
          AND constraint_row.contype = 'u'
    LOOP
        IF constraint_record.column_names =
            ARRAY['event_type', 'stage', 'source', 'event_key', 'revision'] THEN
            EXECUTE format('ALTER TABLE public.event_lake DROP CONSTRAINT %I',
                           constraint_record.conname);
            removed_constraints := removed_constraints + 1;
        END IF;
    END LOOP;
    IF removed_constraints = 0 THEN
        RAISE EXCEPTION 'Expected legacy revision uniqueness constraint not found';
    END IF;
END;
$$;

UPDATE public.event_lake lake
SET entity_id = plan.entity_id,
    revision = plan.migrated_revision,
    supersedes_event_id = plan.migrated_previous,
    subjects = plan.migrated_subjects,
    parent_event_ids = plan.migrated_parents,
    content_hash = encode(sha256(convert_to(jsonb_build_object(
        'schema_version', plan.schema_version, 'payload_version', plan.payload_version,
        'source_event_id', plan.source_event_id,
        'occurred_at', plan.occurred_at, 'event_day', plan.event_day,
        'subjects', plan.migrated_subjects, 'parent_event_ids', plan.migrated_parents,
        'payload', plan.payload, 'evidence', plan.evidence
    )::text, 'UTF8')), 'hex')
FROM event_lake_entity_plan plan
WHERE lake.event_id = plan.event_id AND plan.migrated_event_id = plan.event_id;

INSERT INTO public.event_lake (
    event_id, entity_id, schema_version, event_type, payload_version, stage, source,
    source_event_id, event_key, revision, supersedes_event_id, content_hash,
    occurred_at, event_day, observed_at, ingested_at, subjects, parent_event_ids,
    payload, raw_payload, evidence, provenance, created_at, updated_at, last_seen_at
)
SELECT migrated_event_id, entity_id, schema_version, event_type, payload_version,
       stage, source, source_event_id, event_key, migrated_revision, migrated_previous,
       encode(sha256(convert_to(jsonb_build_object(
           'schema_version', schema_version, 'payload_version', payload_version,
           'source_event_id', source_event_id,
           'occurred_at', occurred_at, 'event_day', event_day,
           'subjects', migrated_subjects, 'parent_event_ids', migrated_parents,
           'payload', payload, 'evidence', evidence
       )::text, 'UTF8')), 'hex'),
       occurred_at, event_day, observed_at, ingested_at, migrated_subjects,
       migrated_parents, payload, raw_payload, evidence, provenance,
       created_at, updated_at, last_seen_at
FROM event_lake_entity_plan
WHERE migrated_event_id <> event_id
ORDER BY migrated_revision;

DO $$
DECLARE
    constraint_record record;
BEGIN
    FOR constraint_record IN SELECT * FROM event_lake_entity_foreign_keys LOOP
        EXECUTE format('ALTER TABLE public.event_lake ADD CONSTRAINT %I %s',
                       constraint_record.conname, constraint_record.definition);
        EXECUTE format('ALTER TABLE public.event_lake VALIDATE CONSTRAINT %I',
                       constraint_record.conname);
    END LOOP;
END;
$$;

ALTER TABLE public.event_lake
    ADD CONSTRAINT event_lake_entity_revision_key
    UNIQUE NULLS NOT DISTINCT (event_type, stage, source, event_key, entity_id, revision);
CREATE INDEX ix_event_lake_entity
    ON public.event_lake(entity_id, event_type, stage, occurred_at DESC)
    WHERE entity_id IS NOT NULL;

DO $$
BEGIN
    IF (SELECT count(*) FROM public.event_lake) <>
       (SELECT count(*) FROM event_lake_entity_migration.event_map)
       OR EXISTS (
           SELECT FROM event_lake_entity_migration.original original
           WHERE NOT EXISTS (
               SELECT FROM public.event_lake lake WHERE lake.event_id = original.event_id
           )
       ) THEN
        RAISE EXCEPTION 'Migrated row count or original UUID preservation failed';
    END IF;
    IF EXISTS (
        SELECT FROM event_lake_entity_plan plan
        LEFT JOIN public.event_lake lake ON lake.event_id = plan.migrated_event_id
        WHERE lake.event_id IS NULL
           OR lake.entity_id IS DISTINCT FROM plan.entity_id
           OR lake.revision <> plan.migrated_revision
           OR lake.supersedes_event_id IS DISTINCT FROM plan.migrated_previous
           OR lake.subjects IS DISTINCT FROM plan.migrated_subjects
           OR lake.parent_event_ids IS DISTINCT FROM plan.migrated_parents
           OR lake.created_at IS DISTINCT FROM plan.created_at
           OR lake.updated_at IS DISTINCT FROM plan.updated_at
           OR lake.ingested_at IS DISTINCT FROM plan.ingested_at
           OR lake.last_seen_at IS DISTINCT FROM plan.last_seen_at
    ) OR EXISTS (
        SELECT FROM public.event_lake lake
        CROSS JOIN LATERAL unnest(lake.parent_event_ids) parent(event_id)
        WHERE NOT EXISTS (
            SELECT FROM public.event_lake referenced WHERE referenced.event_id = parent.event_id
        )
    ) THEN
        RAISE EXCEPTION 'Migrated event integrity or timestamp preservation failed';
    END IF;
END;
$$;

ALTER TABLE public.event_lake ENABLE TRIGGER trg_event_lake_timestamps;

SELECT (SELECT count(*) FROM event_lake_entity_migration.original) AS original_events,
       count(*) AS migrated_events,
       count(*) FILTER (WHERE entity_id IS NULL) AS unresolved_events
FROM public.event_lake;

COMMIT;