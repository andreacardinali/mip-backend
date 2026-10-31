BEGIN;

SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.entities, public.instruments, public.news_sources IN SHARE MODE;

CREATE SCHEMA event_lake_backfill;
CREATE TABLE event_lake_backfill.source_rows (
    table_name text NOT NULL,
    legacy_key text NOT NULL,
    source_schema text NOT NULL,
    row_data jsonb NOT NULL,
    PRIMARY KEY (table_name, legacy_key)
);
CREATE TABLE event_lake_backfill.event_map (
    table_name text NOT NULL,
    legacy_key text NOT NULL,
    entity_id uuid,
    event_id uuid NOT NULL REFERENCES public.event_lake(event_id),
    envelope jsonb NOT NULL,
    reused boolean NOT NULL,
    FOREIGN KEY (table_name, legacy_key)
        REFERENCES event_lake_backfill.source_rows(table_name, legacy_key),
    UNIQUE NULLS NOT DISTINCT (table_name, legacy_key, entity_id)
);

DO $$
DECLARE
    source_record record;
    source_relation regclass;
    source_schema text;
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.event_lake'::regclass
          AND attname = 'entity_id' AND NOT attisdropped
    ) OR to_regprocedure('public.sp_save_event(jsonb)') IS NULL THEN
        RAISE EXCEPTION 'Install the entity-scoped lake and writer first';
    END IF;
    FOR source_record IN
        SELECT * FROM (VALUES
            ('news_clusters', 'cluster_id'), ('news', 'news_id'),
            ('news_entities', 'news_entity_id'), ('news_impacts', 'impact_id'),
            ('catalysts', 'catalyst_id'), ('catalyst_history', 'id'),
            ('filings', 'filing_id'), ('collection_intelligence_events', NULL)
        ) AS sources(table_name, primary_key)
    LOOP
        source_relation := to_regclass(format('event_legacy.%I', source_record.table_name));
        source_schema := 'event_legacy';
        IF source_relation IS NULL THEN
            source_relation := to_regclass(format('public.%I', source_record.table_name));
            source_schema := 'public';
        END IF;
        IF source_relation IS NULL OR NOT EXISTS (
            SELECT FROM pg_catalog.pg_class
            WHERE oid = source_relation AND relkind IN ('r', 'p')
        ) THEN
            RAISE EXCEPTION 'Legacy source table not found: %', source_record.table_name;
        END IF;
        EXECUTE format('LOCK TABLE %s IN SHARE MODE', source_relation);
        EXECUTE format(
            'INSERT INTO event_lake_backfill.source_rows
             SELECT $1, CASE WHEN $2 IS NULL
                 THEN jsonb_build_array(row_data->>''branch'', row_data->>''event_key'')::text
                 ELSE row_data->>$2 END, $3, row_data
             FROM (SELECT to_jsonb(original) AS row_data FROM %s original) snapshot',
            source_relation
        ) USING source_record.table_name, source_record.primary_key, source_schema;
    END LOOP;
END;
$$;

CREATE TEMP TABLE event_lake_backfill_plan (
    sequence integer NOT NULL,
    table_name text NOT NULL,
    legacy_key text NOT NULL,
    entity_id uuid,
    envelope jsonb NOT NULL,
    UNIQUE NULLS NOT DISTINCT (table_name, legacy_key, entity_id)
) ON COMMIT DROP;

INSERT INTO public.event_type_catalog (
    event_type, payload_version, allowed_stages, required_payload_types
)
VALUES
    ('NEWS_ARTICLE', 2, ARRAY['ACQUIRED'], '{"news_id":"string"}'),
    ('NEWS_ASSOCIATION', 2, ARRAY['ACQUIRED'], '{"news_entity_id":"string"}'),
    ('NEWS_CLUSTER', 2, ARRAY['ACQUIRED'], '{"cluster_id":"string"}'),
    ('NEWS_IMPACT', 2, ARRAY['ENRICHED'], '{"impact_id":"string"}'),
    ('CATALYST', 2, ARRAY['ACQUIRED'], '{"catalyst_id":"string"}'),
    ('CATALYST_HISTORY', 2, ARRAY['ACQUIRED'], '{"id":"string"}'),
    ('FILING', 2, ARRAY['ACQUIRED'], '{"filing_id":"string"}')
ON CONFLICT (event_type, payload_version) DO NOTHING;

INSERT INTO event_lake_backfill_plan
SELECT 10, table_name, legacy_key, NULL,
    jsonb_build_object(
        'event_type', 'NEWS_CLUSTER', 'stage', 'ACQUIRED', 'source', 'NEWS_INGESTION',
        'event_key', legacy_key, 'occurred_at', row_data->'created_at',
        'payload', row_data
    )
FROM event_lake_backfill.source_rows WHERE table_name = 'news_clusters';

INSERT INTO event_lake_backfill_plan
SELECT 20, article.table_name, article.legacy_key, association.entity_id,
    jsonb_build_object(
        'event_type', 'NEWS_ARTICLE', 'stage', 'ACQUIRED',
        'source', coalesce(nullif(btrim(source.name), ''), 'UNKNOWN'),
        'event_key', article.legacy_key,
        'source_event_id', coalesce(nullif(article.row_data->'raw_json'->>'external_id', ''),
                                    nullif(article.row_data->'raw_json'->>'id', '')),
        'occurred_at', article.row_data->'published_at',
        'payload', (article.row_data - 'raw_json' - 'title' - 'published_at')
            || jsonb_build_object('headline', article.row_data->'title'),
        'raw_payload', article.row_data->'raw_json'
    )
FROM event_lake_backfill.source_rows article
LEFT JOIN public.news_sources source ON source.source_id = (article.row_data->>'source_id')::uuid
LEFT JOIN LATERAL (
    SELECT DISTINCT (row_data->>'entity_id')::uuid AS entity_id
    FROM event_lake_backfill.source_rows
        WHERE table_name IN ('news_entities', 'news_impacts')
            AND row_data->>'news_id' = article.legacy_key
) association ON true
WHERE article.table_name = 'news';

INSERT INTO event_lake_backfill_plan
SELECT 30, table_name, legacy_key, (row_data->>'entity_id')::uuid,
    jsonb_build_object(
        'event_type', 'NEWS_ASSOCIATION', 'stage', 'ACQUIRED', 'source', 'NEWS_INGESTION',
        'event_key', legacy_key, 'payload', row_data
    )
FROM event_lake_backfill.source_rows WHERE table_name = 'news_entities';

INSERT INTO event_lake_backfill_plan
SELECT 40, table_name, legacy_key, (row_data->>'entity_id')::uuid,
    jsonb_build_object(
        'event_type', 'NEWS_IMPACT', 'stage', 'ENRICHED', 'source', 'NEWS_ENRICHMENT',
        'event_key', legacy_key, 'payload', row_data
    )
FROM event_lake_backfill.source_rows WHERE table_name = 'news_impacts';

INSERT INTO event_lake_backfill_plan
SELECT 50, table_name, legacy_key, (row_data->>'entity_id')::uuid,
    jsonb_build_object(
        'event_type', 'CATALYST', 'stage', 'ACQUIRED',
        'source', coalesce(nullif(btrim(row_data->>'source'), ''), 'UNKNOWN'),
        'event_key', legacy_key,
        'source_event_id', coalesce(nullif(row_data->'raw_payload'->>'eventId', ''),
                                    nullif(row_data->'raw_payload'->>'id', '')),
        'occurred_at', row_data->'catalyst_date', 'event_day', row_data->'catalyst_day',
        'payload', (row_data - 'raw_payload' - 'source') || jsonb_build_object(
            'subtype', row_data->'catalyst_type', 'importance', row_data->>'importance',
            'details', CASE WHEN jsonb_typeof(row_data->'raw_payload') = 'object'
                            THEN row_data->'raw_payload' ELSE '{}'::jsonb END
        ), 'raw_payload', row_data->'raw_payload'
    )
FROM event_lake_backfill.source_rows WHERE table_name = 'catalysts';

INSERT INTO event_lake_backfill_plan
SELECT 60, history.table_name, history.legacy_key, (catalyst.row_data->>'entity_id')::uuid,
    jsonb_build_object(
        'event_type', 'CATALYST_HISTORY', 'stage', 'ACQUIRED', 'source', 'CATALYST_HISTORY',
        'event_key', history.legacy_key, 'payload', history.row_data
    )
FROM event_lake_backfill.source_rows history
LEFT JOIN event_lake_backfill.source_rows catalyst
    ON catalyst.table_name = 'catalysts' AND catalyst.legacy_key = history.row_data->>'catalyst_id'
WHERE history.table_name = 'catalyst_history';

INSERT INTO event_lake_backfill_plan
SELECT 70, table_name, legacy_key, (row_data->>'entity_id')::uuid,
    jsonb_build_object(
        'event_type', 'FILING', 'stage', 'ACQUIRED',
        'source', coalesce(nullif(btrim(row_data->>'source'), ''), 'UNKNOWN'),
        'event_key', legacy_key, 'source_event_id', row_data->'accession_number',
        'occurred_at', row_data->'accepted_at', 'event_day', row_data->'filing_date',
        'payload', row_data - 'source' - 'raw_payload', 'raw_payload', row_data->'raw_payload'
    )
FROM event_lake_backfill.source_rows WHERE table_name = 'filings';

INSERT INTO public.event_type_catalog
    (event_type, payload_version, required_payload_types, allowed_stages)
VALUES
    ('DAILY_MOVER', 2, '{}'::jsonb, ARRAY['VALIDATED']),
    ('UPCOMING_CATALYST', 2, '{}'::jsonb, ARRAY['VALIDATED']),
    ('BREAKING_CATALYST', 2, '{}'::jsonb, ARRAY['VALIDATED'])
ON CONFLICT (event_type, payload_version) DO NOTHING;

INSERT INTO event_lake_backfill_plan
SELECT 80, table_name, legacy_key, (row_data->>'entity_id')::uuid,
       jsonb_build_object(
           'event_type', row_data->>'branch', 'stage', 'VALIDATED',
           'source', 'COLLECTION_RESEARCH',
           'event_key', row_data->>'event_key',
           'event_day', CASE
               WHEN row_data->>'branch' = 'DAILY_MOVER'
                   THEN nullif(split_part(row_data->>'event_key', '|', 2), '')
               ELSE row_data->'payload'->>'eventDate'
           END,
           'payload', row_data->'payload', 'evidence', row_data->'evidence',
           'observed_at', row_data->'last_seen_at')
FROM event_lake_backfill.source_rows
WHERE table_name = 'collection_intelligence_events';

CREATE FUNCTION event_lake_backfill.stable_key(identity_parts jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    encoded text := '';
    character_value text;
    codepoint integer;
BEGIN
    FOREACH character_value IN ARRAY regexp_split_to_array(identity_parts::text, '') LOOP
        codepoint := ascii(character_value);
        encoded := encoded || CASE
            WHEN codepoint < 128 THEN character_value
            WHEN codepoint <= 65535 THEN E'\\u' || lpad(to_hex(codepoint), 4, '0')
            ELSE E'\\u' || to_hex(55296 + ((codepoint - 65536) / 1024))
                 || E'\\u' || to_hex(56320 + ((codepoint - 65536) % 1024))
        END;
    END LOOP;
    RETURN encode(sha256(convert_to(encoded, 'UTF8')), 'hex');
END;
$$;

UPDATE event_lake_backfill_plan plan
SET envelope = plan.envelope || jsonb_build_object(
    'event_key', CASE
        WHEN original.row_data->>'branch' = 'DAILY_MOVER' THEN
            event_lake_backfill.stable_key(jsonb_build_array(
                original.row_data->>'instrument_id',
                coalesce(plan.envelope->>'event_day', original.row_data->>'event_key')))
        WHEN original.row_data->>'branch' = 'UPCOMING_CATALYST'
             AND plan.envelope->>'event_day' IS NOT NULL THEN
            event_lake_backfill.stable_key(jsonb_build_array(
                original.row_data->>'entity_id', original.row_data->'payload'->>'eventType',
                CASE
                    WHEN coalesce(nullif(original.row_data->'payload'->>'eventId', ''),
                                  nullif(original.row_data->'payload'->>'id', '')) IS NOT NULL
                    THEN jsonb_build_array('provider', coalesce(
                        nullif(original.row_data->'payload'->>'eventId', ''),
                        nullif(original.row_data->'payload'->>'id', '')))
                    WHEN upper(original.row_data->'payload'->>'eventType') LIKE '%EARNING%'
                         AND original.row_data->'payload'->>'fiscalYear' IS NOT NULL
                         AND coalesce(original.row_data->'payload'->>'fiscalQuarter',
                                      original.row_data->'payload'->>'fiscalPeriod') IS NOT NULL
                    THEN jsonb_build_array('fiscal', original.row_data->'payload'->>'fiscalYear',
                        coalesce(original.row_data->'payload'->>'fiscalQuarter',
                                 original.row_data->'payload'->>'fiscalPeriod'))
                    ELSE jsonb_build_array('date', plan.envelope->>'event_day')
                END))
        ELSE original.row_data->>'event_key'
    END,
    'subjects', jsonb_build_array(jsonb_build_object(
        'entity_id', plan.entity_id, 'instrument_id', original.row_data->>'instrument_id')),
    'payload', (original.row_data->'payload') - ARRAY[
        'daysUntilEvent', 'eventKey', 'sources', 'relatedSymbols', 'name', 'symbol',
        'entity_id', 'observedDate', 'marketEvidence'],
    'evidence', (original.row_data->'evidence') - 'marketEvidence',
    'raw_payload', original.row_data->'payload')
FROM event_lake_backfill.source_rows original
WHERE plan.table_name = 'collection_intelligence_events'
  AND original.table_name = plan.table_name AND original.legacy_key = plan.legacy_key;

UPDATE event_lake_backfill_plan plan
SET envelope = jsonb_build_object(
        'schema_version', 1, 'payload_version', 2,
        'subjects', CASE WHEN plan.entity_id IS NULL THEN '[]'::jsonb
            ELSE jsonb_build_array(jsonb_build_object('entity_id', plan.entity_id)) END,
        'parent_event_ids', '[]'::jsonb, 'evidence', '{}'::jsonb,
        'provenance', jsonb_build_object(
            'legacy_table', original.table_name,
            'legacy_schema', original.source_schema,
            'legacy_key', original.legacy_key,
            'legacy_row', original.row_data)
    ) || plan.envelope
FROM event_lake_backfill.source_rows original
WHERE original.table_name = plan.table_name AND original.legacy_key = plan.legacy_key;

UPDATE event_lake_backfill_plan
SET envelope = envelope || jsonb_build_object('raw_payload', NULL)
WHERE envelope->'raw_payload' <> 'null'::jsonb
    AND jsonb_typeof(envelope->'raw_payload') <> 'object';

DO $$
DECLARE
    planned record;
    candidate uuid;
    saved uuid;
    candidates integer;
    parents jsonb;
    logical_id text;
    parent_logical_id uuid;
BEGIN
    IF EXISTS (
        SELECT FROM event_lake_backfill_plan plan
        WHERE plan.entity_id IS NOT NULL AND NOT EXISTS (
            SELECT FROM public.entities entity WHERE entity.entity_id = plan.entity_id
        )
    ) THEN
        RAISE EXCEPTION 'Backfill contains unknown entities';
    END IF;
    IF EXISTS (
        SELECT FROM event_lake_backfill.source_rows original
        WHERE original.table_name = 'collection_intelligence_events'
          AND NOT EXISTS (
              SELECT FROM public.instruments instrument
              WHERE instrument.instrument_id = (original.row_data->>'instrument_id')::uuid
                AND instrument.entity_id = (original.row_data->>'entity_id')::uuid
          )
    ) THEN
        RAISE EXCEPTION 'Backfill contains inconsistent instrument subjects';
    END IF;
    IF EXISTS (
        SELECT FROM event_lake_backfill.source_rows original
        WHERE original.table_name IN ('news_entities', 'news_impacts')
          AND original.row_data->>'news_id' IS NOT NULL
          AND NOT EXISTS (
              SELECT FROM event_lake_backfill.source_rows article
              WHERE article.table_name = 'news'
                AND article.legacy_key = original.row_data->>'news_id'
          )
    ) OR EXISTS (
        SELECT FROM event_lake_backfill.source_rows original
        WHERE original.table_name = 'catalyst_history'
          AND original.row_data->>'catalyst_id' IS NOT NULL
          AND NOT EXISTS (
              SELECT FROM event_lake_backfill.source_rows catalyst
              WHERE catalyst.table_name = 'catalysts'
                AND catalyst.legacy_key = original.row_data->>'catalyst_id'
          )
    ) OR EXISTS (
        SELECT FROM event_lake_backfill.source_rows original
        WHERE original.table_name = 'news'
          AND original.row_data->>'cluster_id' IS NOT NULL
          AND NOT EXISTS (
              SELECT FROM event_lake_backfill.source_rows cluster
              WHERE cluster.table_name = 'news_clusters'
                AND cluster.legacy_key = original.row_data->>'cluster_id'
          )
    ) THEN
        RAISE EXCEPTION 'Backfill contains orphan legacy references';
    END IF;

    FOR planned IN
        SELECT * FROM event_lake_backfill_plan
        ORDER BY sequence, legacy_key, entity_id NULLS FIRST
    LOOP
        SELECT coalesce(jsonb_agg(DISTINCT mapping.event_id ORDER BY mapping.event_id), '[]'::jsonb)
        INTO parents
        FROM event_lake_backfill.source_rows original
        JOIN event_lake_backfill.event_map mapping ON (
            (planned.table_name = 'news' AND mapping.table_name = 'news_clusters'
                AND mapping.legacy_key = original.row_data->>'cluster_id')
            OR (planned.table_name IN ('news_entities', 'news_impacts')
                AND mapping.table_name = 'news'
                AND mapping.legacy_key = original.row_data->>'news_id'
                AND mapping.entity_id IS NOT DISTINCT FROM planned.entity_id)
            OR (planned.table_name = 'catalyst_history' AND mapping.table_name = 'catalysts'
                AND mapping.legacy_key = original.row_data->>'catalyst_id'
                AND mapping.entity_id IS NOT DISTINCT FROM planned.entity_id)
        )
        WHERE original.table_name = planned.table_name
          AND original.legacy_key = planned.legacy_key;
        planned.envelope := planned.envelope || jsonb_build_object('parent_event_ids', parents);
        IF planned.table_name IN ('news_entities', 'news_impacts')
           AND jsonb_array_length(parents) > 0 THEN
            SELECT (lake.payload->>'news_id')::uuid INTO parent_logical_id
            FROM public.event_lake lake WHERE lake.event_id = (parents->>0)::uuid;
            planned.envelope := jsonb_set(planned.envelope, '{payload,news_id}',
                to_jsonb(parent_logical_id));
        ELSIF planned.table_name = 'catalyst_history' AND jsonb_array_length(parents) > 0 THEN
            SELECT coalesce((lake.payload->>'catalyst_id')::uuid, root.event_id)
            INTO parent_logical_id
            FROM public.event_lake lake
            JOIN public.event_lake root
              ON root.event_type = lake.event_type AND root.stage = lake.stage
             AND root.source = lake.source AND root.event_key = lake.event_key
             AND root.entity_id IS NOT DISTINCT FROM lake.entity_id AND root.revision = 1
            WHERE lake.event_id = (parents->>0)::uuid;
            planned.envelope := jsonb_set(planned.envelope, '{payload,catalyst_id}',
                to_jsonb(parent_logical_id));
        END IF;
        logical_id := CASE planned.table_name
            WHEN 'news_clusters' THEN 'cluster_id'
            WHEN 'news' THEN 'news_id'
            WHEN 'news_entities' THEN 'news_entity_id'
            WHEN 'news_impacts' THEN 'impact_id'
            WHEN 'catalysts' THEN 'catalyst_id'
            WHEN 'catalyst_history' THEN 'id'
            WHEN 'filings' THEN 'filing_id'
        END;
        SELECT count(DISTINCT (lake.source, lake.event_key)), min(lake.event_id::text)::uuid
        INTO candidates, candidate
        FROM public.event_lake lake
        WHERE lake.event_type = planned.envelope->>'event_type'
          AND lake.stage = planned.envelope->>'stage'
          AND lake.entity_id IS NOT DISTINCT FROM planned.entity_id
          AND (
              (logical_id IS NOT NULL AND lake.payload->>logical_id = planned.legacy_key)
              OR (lake.source = planned.envelope->>'source'
                  AND lake.event_key = planned.envelope->>'event_key')
              OR (planned.table_name = 'news'
                  AND lake.source = planned.envelope->>'source'
                  AND ((nullif(planned.envelope->>'source_event_id', '') IS NOT NULL
                        AND lake.source_event_id = planned.envelope->>'source_event_id')
                       OR (nullif(planned.envelope->'payload'->>'url', '') IS NOT NULL
                           AND lake.payload->>'url' = planned.envelope->'payload'->>'url')
                       OR (nullif(planned.envelope->'payload'->>'content_hash', '') IS NOT NULL
                           AND lake.payload->>'content_hash' =
                               planned.envelope->'payload'->>'content_hash')))
              OR (planned.table_name IN ('news_entities', 'news_impacts')
                  AND lake.source = planned.envelope->>'source'
                  AND lake.payload->>'news_id' = planned.envelope->'payload'->>'news_id')
              OR (planned.table_name = 'collection_intelligence_events'
                  AND lake.source = planned.envelope->>'source'
                  AND lake.provenance->>'projection_event_key' =
                      planned.envelope->'provenance'->'legacy_row'->>'event_key')
              OR (planned.table_name = 'filings'
                  AND lake.source = planned.envelope->>'source'
                  AND lake.payload->>'cik' = planned.envelope->'payload'->>'cik'
                  AND CASE WHEN nullif(planned.envelope->>'source_event_id', '') IS NOT NULL
                      THEN lake.source_event_id = planned.envelope->>'source_event_id'
                      ELSE lake.payload->>'filing_type' = planned.envelope->'payload'->>'filing_type'
                           AND lake.occurred_at = (planned.envelope->>'occurred_at')::timestamptz END)
              OR (planned.table_name = 'catalysts'
                  AND lake.source = planned.envelope->>'source'
                  AND CASE upper(lake.payload->>'subtype')
                      WHEN 'QUARTERLY_EARNINGS' THEN 'EARNINGS'
                      WHEN 'CASH_DIVIDEND' THEN 'DIVIDEND'
                      WHEN 'STOCK_SPLIT' THEN 'SPLIT'
                      ELSE upper(lake.payload->>'subtype') END =
                      CASE upper(planned.envelope->'payload'->>'subtype')
                      WHEN 'QUARTERLY_EARNINGS' THEN 'EARNINGS'
                      WHEN 'CASH_DIVIDEND' THEN 'DIVIDEND'
                      WHEN 'STOCK_SPLIT' THEN 'SPLIT'
                      ELSE upper(planned.envelope->'payload'->>'subtype') END
                  AND CASE WHEN nullif(planned.envelope->>'source_event_id', '') IS NOT NULL
                      THEN lake.source_event_id = planned.envelope->>'source_event_id'
                      WHEN planned.envelope->'raw_payload'->>'fiscalYear' IS NOT NULL
                           AND coalesce(planned.envelope->'raw_payload'->>'fiscalQuarter',
                                        planned.envelope->'raw_payload'->>'fiscalPeriod') IS NOT NULL
                      THEN lake.raw_payload->>'fiscalYear' = planned.envelope->'raw_payload'->>'fiscalYear'
                           AND coalesce(lake.raw_payload->>'fiscalQuarter', lake.raw_payload->>'fiscalPeriod') =
                               coalesce(planned.envelope->'raw_payload'->>'fiscalQuarter',
                                        planned.envelope->'raw_payload'->>'fiscalPeriod')
                      ELSE lake.source_event_id IS NULL
                           AND lake.event_day = (planned.envelope->>'event_day')::date END)
          );
        IF candidates > 1 THEN
            RAISE EXCEPTION 'Ambiguous existing event for %/%/%',
                planned.table_name, planned.legacy_key, planned.entity_id;
        END IF;
        IF candidate IS NOT NULL THEN
            SELECT lake.event_id INTO saved
            FROM public.event_lake lake
            JOIN public.event_lake matched ON matched.event_id = candidate
            WHERE lake.event_type = matched.event_type AND lake.stage = matched.stage
              AND lake.source = matched.source AND lake.event_key = matched.event_key
              AND lake.entity_id IS NOT DISTINCT FROM matched.entity_id
            ORDER BY lake.revision DESC LIMIT 1;
        ELSE
            saved := public.sp_save_event(planned.envelope);
        END IF;
        INSERT INTO event_lake_backfill.event_map
            (table_name, legacy_key, entity_id, event_id, envelope, reused)
        VALUES (planned.table_name, planned.legacy_key, planned.entity_id,
                saved, planned.envelope, candidate IS NOT NULL);
    END LOOP;
    IF EXISTS (
        SELECT FROM event_lake_backfill_plan plan
        WHERE NOT EXISTS (
            SELECT FROM event_lake_backfill.event_map mapping
            WHERE mapping.table_name = plan.table_name AND mapping.legacy_key = plan.legacy_key
              AND mapping.entity_id IS NOT DISTINCT FROM plan.entity_id
        )
    ) OR EXISTS (
        SELECT FROM event_lake_backfill.source_rows original
        WHERE NOT EXISTS (
            SELECT FROM event_lake_backfill.event_map mapping
            WHERE mapping.table_name = original.table_name AND mapping.legacy_key = original.legacy_key
        )
    ) THEN
        RAISE EXCEPTION 'Incomplete legacy backfill';
    END IF;
END;
$$;

SELECT table_name, count(*) AS mapped_entities,
       count(*) FILTER (WHERE reused) AS reused,
       count(*) FILTER (WHERE NOT reused) AS imported
FROM event_lake_backfill.event_map
GROUP BY table_name ORDER BY table_name;

COMMIT;