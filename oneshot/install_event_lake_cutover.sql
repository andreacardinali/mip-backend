BEGIN;

SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.news, public.news_entities, public.news_clusters,
    public.news_impacts, public.catalysts, public.catalyst_history,
    public.filings, public.collection_intelligence_events IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    source_record record;
    missing_rows boolean;
BEGIN
    IF to_regclass('public.event_lake_latest') IS NOT NULL
       OR to_regnamespace('event_legacy') IS NOT NULL THEN
        RAISE EXCEPTION 'Cutover already installed or event_legacy requires manual review';
    END IF;
    IF to_regclass('event_lake_backfill.source_rows') IS NULL
       OR to_regclass('event_lake_backfill.event_map') IS NULL
       OR to_regprocedure('public.sp_save_event(jsonb)') IS NULL THEN
        RAISE EXCEPTION 'Install the lake and complete the historical backfill first';
    END IF;
    FOR source_record IN
        SELECT * FROM (VALUES
            ('news_clusters', 'cluster_id'), ('news', 'news_id'),
            ('news_entities', 'news_entity_id'), ('news_impacts', 'impact_id'),
            ('catalysts', 'catalyst_id'), ('catalyst_history', 'id'),
            ('filings', 'filing_id'), ('collection_intelligence_events', NULL)
        ) AS sources(table_name, primary_key)
    LOOP
        IF NOT EXISTS (
            SELECT FROM pg_catalog.pg_class
            WHERE oid = to_regclass(format('public.%I', source_record.table_name))
              AND relkind IN ('r', 'p')
        ) THEN
            RAISE EXCEPTION 'Expected physical legacy table: %', source_record.table_name;
        END IF;
        EXECUTE format(
            'SELECT EXISTS (
                SELECT FROM (SELECT to_jsonb(original) AS row_data FROM public.%I original) current_rows
                WHERE NOT EXISTS (
                    SELECT FROM event_lake_backfill.source_rows snapshot
                    WHERE snapshot.table_name = $1
                      AND snapshot.legacy_key = CASE WHEN $2 IS NULL
                          THEN jsonb_build_array(current_rows.row_data->>''branch'', current_rows.row_data->>''event_key'')::text
                          ELSE current_rows.row_data->>$2 END
                      AND snapshot.row_data = current_rows.row_data
                      AND EXISTS (
                          SELECT FROM event_lake_backfill.event_map mapping
                          JOIN public.event_lake lake ON lake.event_id = mapping.event_id
                          WHERE mapping.table_name = snapshot.table_name
                            AND mapping.legacy_key = snapshot.legacy_key
                      )
                )
            )', source_record.table_name
        ) INTO missing_rows USING source_record.table_name, source_record.primary_key;
        IF missing_rows THEN
            RAISE EXCEPTION 'Legacy data changed or backfill incomplete: %', source_record.table_name;
        END IF;
    END LOOP;
END;
$$;

CREATE VIEW public.event_lake_latest AS
SELECT DISTINCT ON (event_type, stage, source, event_key, entity_id) *
FROM public.event_lake
ORDER BY event_type, stage, source, event_key, entity_id, revision DESC;

CREATE SCHEMA event_legacy;
ALTER TABLE public.news SET SCHEMA event_legacy;
ALTER TABLE public.news_entities SET SCHEMA event_legacy;
ALTER TABLE public.news_clusters SET SCHEMA event_legacy;
ALTER TABLE public.news_impacts SET SCHEMA event_legacy;
ALTER TABLE public.catalysts SET SCHEMA event_legacy;
ALTER TABLE public.catalyst_history SET SCHEMA event_legacy;
ALTER TABLE public.filings SET SCHEMA event_legacy;
ALTER TABLE public.collection_intelligence_events SET SCHEMA event_legacy;

CREATE VIEW public.news AS
SELECT DISTINCT ON ((payload->>'news_id')::uuid)
    (payload->>'news_id')::uuid AS news_id,
       (payload->>'cluster_id')::uuid AS cluster_id,
       (payload->>'source_id')::uuid AS source_id,
       payload->>'headline' AS title, payload->>'summary' AS summary,
       payload->>'url' AS url,
       (payload->>'content_hash')::varchar(64) AS content_hash,
       occurred_at AS published_at, raw_payload AS raw_json
FROM public.event_lake_latest
WHERE event_type = 'NEWS_ARTICLE' AND stage = 'ACQUIRED'
ORDER BY (payload->>'news_id')::uuid, created_at DESC, event_id;

CREATE VIEW public.news_entities AS
SELECT (association.payload->>'news_entity_id')::uuid AS news_entity_id,
       (association.payload->>'news_id')::uuid AS news_id,
         association.entity_id,
       (coalesce(score.payload, association.payload)->>'relevance')::numeric AS relevance,
       (coalesce(score.payload, association.payload)->>'sentiment')::numeric AS sentiment,
       (coalesce(score.payload, association.payload)->>'impact')::numeric AS impact
FROM public.event_lake_latest association
LEFT JOIN public.event_lake_latest score
  ON score.event_type = 'NEWS_SCORE' AND score.stage = 'ENRICHED'
 AND score.source = 'NEWS_ENRICHMENT'
 AND score.entity_id = association.entity_id
 AND score.event_key = association.payload->>'news_entity_id'
WHERE association.event_type = 'NEWS_ASSOCIATION' AND association.stage = 'ACQUIRED';

CREATE VIEW public.news_clusters AS
SELECT (payload->>'cluster_id')::uuid AS cluster_id, payload->>'title' AS title,
             coalesce((payload->>'created_at')::timestamptz, created_at) AS created_at
FROM public.event_lake_latest
WHERE event_type = 'NEWS_CLUSTER' AND stage = 'ACQUIRED';

CREATE VIEW public.news_impacts AS
SELECT (payload->>'impact_id')::uuid AS impact_id,
             (payload->>'news_id')::uuid AS news_id,
             entity_id,
             (payload->>'direct_impact')::numeric AS direct_impact,
             (payload->>'propagated_impact')::numeric AS propagated_impact,
             (payload->>'confidence')::numeric AS confidence
FROM public.event_lake_latest
WHERE event_type = 'NEWS_IMPACT' AND stage = 'ENRICHED';

CREATE VIEW public.catalysts AS
SELECT coalesce((lake.payload->>'catalyst_id')::uuid, root.event_id) AS catalyst_id,
             lake.entity_id,
             (CASE upper(lake.payload->>'subtype')
                     WHEN 'QUARTERLY_EARNINGS' THEN 'EARNINGS'
                     WHEN 'CASH_DIVIDEND' THEN 'DIVIDEND'
                     WHEN 'STOCK_SPLIT' THEN 'SPLIT'
                     ELSE upper(lake.payload->>'subtype') END)::varchar(50) AS catalyst_type,
             lake.occurred_at AS catalyst_date,
             (lake.payload->>'status')::varchar(30) AS status,
             CASE upper(lake.payload->>'importance')
                     WHEN 'HIGH' THEN 90 WHEN 'MEDIUM' THEN 50 WHEN 'LOW' THEN 10
                     ELSE (lake.payload->>'importance')::numeric END AS importance,
             lake.payload->>'title' AS title, lake.source::varchar(50) AS source,
             lake.raw_payload AS raw_payload, lake.event_day AS catalyst_day
FROM public.event_lake_latest lake
JOIN public.event_lake root
    ON (root.event_type, root.stage, root.source, root.event_key) =
         (lake.event_type, lake.stage, lake.source, lake.event_key) AND root.revision = 1
       AND root.entity_id IS NOT DISTINCT FROM lake.entity_id
WHERE lake.event_type = 'CATALYST' AND lake.stage = 'ACQUIRED';

CREATE VIEW public.catalyst_history AS
SELECT (payload->>'id')::uuid AS id, (payload->>'catalyst_id')::uuid AS catalyst_id,
             (payload->>'previous_status')::varchar(30) AS previous_status,
             (payload->>'new_status')::varchar(30) AS new_status
FROM public.event_lake_latest
WHERE event_type = 'CATALYST_HISTORY' AND stage = 'ACQUIRED';

CREATE VIEW public.filings AS
SELECT coalesce((lake.payload->>'filing_id')::uuid, root.event_id) AS filing_id,
             lake.entity_id,
             (lake.payload->>'filing_type')::varchar(20) AS filing_type,
             lake.event_day AS filing_date, lake.occurred_at AS accepted_at,
             (lake.payload->>'cik')::varchar(20) AS cik,
             (lake.payload->>'accession_number')::varchar(50) AS accession_number,
             lake.payload->>'title' AS title, lake.source::varchar(50) AS source,
             lake.raw_payload AS raw_payload,
             coalesce((lake.payload->>'created_at')::timestamptz, root.created_at) AS created_at,
             (lake.payload->>'filing_subtype')::varchar(50) AS filing_subtype
FROM public.event_lake_latest lake
JOIN public.event_lake root
    ON (root.event_type, root.stage, root.source, root.event_key) =
         (lake.event_type, lake.stage, lake.source, lake.event_key) AND root.revision = 1
       AND root.entity_id IS NOT DISTINCT FROM lake.entity_id
WHERE lake.event_type = 'FILING' AND lake.stage = 'ACQUIRED';

CREATE VIEW public.collection_intelligence_events AS
SELECT event_type AS branch,
             coalesce(provenance->>'projection_event_key', event_key) AS event_key,
             lake.entity_id,
             (subjects->0->>'instrument_id')::uuid AS instrument_id,
             coalesce(provenance->>'category', event_type) AS category,
             payload || jsonb_build_object(
                     'entity_id', lake.entity_id,
                     'instrument_id', subjects->0->>'instrument_id'
             ) AS payload, evidence,
             (SELECT min(original.created_at) FROM public.event_lake original
                WHERE (original.event_type, original.stage, original.source, original.event_key) =
                                                        (lake.event_type, lake.stage, lake.source, lake.event_key)
                                    AND original.entity_id IS NOT DISTINCT FROM lake.entity_id) AS first_seen_at,
             last_seen_at
FROM public.event_lake_latest lake
WHERE stage = 'VALIDATED'
    AND event_type IN ('DAILY_MOVER', 'UPCOMING_CATALYST', 'BREAKING_CATALYST');

CREATE OR REPLACE FUNCTION public.sp_news_upsert_article(p_entity_id uuid, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    v_source_id uuid;
    v_source_name varchar(100);
    v_title text;
    v_url text;
    v_published_at timestamptz;
    v_key text;
    v_previous public.event_lake%ROWTYPE;
    v_news_id uuid;
    v_news_entity_id uuid;
    v_article_event_id uuid;
    v_inserted boolean;
    v_association_inserted boolean;
    v_payload jsonb;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'p_payload must be a JSON object';
    END IF;
    IF p_entity_id IS NULL OR NOT EXISTS (
        SELECT FROM public.entities WHERE entity_id = p_entity_id
    ) THEN
        RAISE EXCEPTION 'Entity not found: %', p_entity_id;
    END IF;
    v_source_name := left(coalesce(
        nullif(btrim(p_payload->>'source_name'), ''),
        nullif(btrim(p_payload->>'publisher'), ''),
        nullif(btrim(p_payload->>'source'), ''),
        nullif(btrim(p_payload->>'provider'), ''), 'UNKNOWN'
    ), 100);
    v_title := coalesce(nullif(btrim(p_payload->>'headline'), ''),
                        nullif(btrim(p_payload->>'title'), ''));
    IF v_title IS NULL THEN
        RAISE EXCEPTION 'News title/headline is required';
    END IF;
    v_url := coalesce(nullif(btrim(p_payload->>'url'), ''),
                      nullif(btrim(p_payload->>'link'), ''));
    v_published_at := coalesce(
        nullif(p_payload->>'published_at', '')::timestamptz,
        nullif(p_payload->>'publishedAt', '')::timestamptz,
        nullif(p_payload->>'date', '')::timestamptz
    );
    v_key := encode(sha256(convert_to(jsonb_build_array(
        lower(v_source_name), coalesce(
            nullif(btrim(p_payload->>'external_id'), ''),
            nullif(btrim(p_payload->>'id'), ''), v_url,
            jsonb_build_array(lower(v_title), v_published_at)::text
        )
    )::text, 'UTF8')), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended('news:' || lower(v_source_name), 0));
    SELECT * INTO v_previous FROM public.event_lake_latest
    WHERE event_type = 'NEWS_ARTICLE' AND stage = 'ACQUIRED'
      AND source = v_source_name
      AND (event_key = v_key OR (v_url IS NOT NULL AND payload->>'url' = v_url)
           OR (coalesce(nullif(p_payload->>'external_id', ''), nullif(p_payload->>'id', ''))
               IS NOT NULL AND source_event_id = coalesce(
                   nullif(p_payload->>'external_id', ''), nullif(p_payload->>'id', ''))))
    ORDER BY created_at DESC, revision DESC, event_id LIMIT 1;
    v_inserted := NOT FOUND;
    v_key := coalesce(v_previous.event_key, v_key);
    v_news_id := coalesce((v_previous.payload->>'news_id')::uuid, gen_random_uuid());
    INSERT INTO public.news_sources(name) VALUES (v_source_name)
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING source_id INTO v_source_id;
    v_payload := coalesce(v_previous.payload, '{}'::jsonb) || jsonb_build_object(
        'news_id', v_news_id, 'source_id', v_source_id, 'headline', v_title,
        'summary', coalesce(nullif(btrim(p_payload->>'summary'), ''), v_previous.payload->>'summary'),
        'url', coalesce(v_url, v_previous.payload->>'url'),
        'content_hash', coalesce(v_previous.payload->>'content_hash', v_key)
    );
    v_article_event_id := public.sp_save_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1, 'event_type', 'NEWS_ARTICLE',
        'stage', 'ACQUIRED', 'source', v_source_name, 'event_key', v_key,
        'source_event_id', coalesce(nullif(p_payload->>'external_id', ''),
                                    nullif(p_payload->>'id', ''), v_previous.source_event_id),
        'occurred_at', coalesce(v_published_at, v_previous.occurred_at),
        'subjects', jsonb_build_array(jsonb_build_object('entity_id', p_entity_id)),
        'parent_event_ids', '[]'::jsonb,
        'payload', v_payload,
        'raw_payload', coalesce(v_previous.raw_payload, '{}'::jsonb) || p_payload,
        'evidence', '{}'::jsonb, 'provenance', '{}'::jsonb
    ));
        SELECT (association.payload->>'news_entity_id')::uuid INTO v_news_entity_id
        FROM public.event_lake_latest association
        WHERE association.event_type = 'NEWS_ASSOCIATION' AND association.stage = 'ACQUIRED'
            AND (association.payload->>'news_id')::uuid = v_news_id
            AND association.entity_id = p_entity_id;
    v_association_inserted := NOT FOUND;
    IF v_association_inserted THEN
        v_news_entity_id := gen_random_uuid();
        PERFORM public.sp_save_event(jsonb_build_object(
            'schema_version', 1, 'payload_version', 1, 'event_type', 'NEWS_ASSOCIATION',
            'stage', 'ACQUIRED', 'source', 'NEWS_INGESTION',
            'event_key', v_news_entity_id::text,
            'subjects', jsonb_build_array(jsonb_build_object('entity_id', p_entity_id)),
            'parent_event_ids', jsonb_build_array(v_article_event_id),
            'payload', jsonb_build_object('news_entity_id', v_news_entity_id,
                'news_id', v_news_id, 'entity_id', p_entity_id,
                'relevance', nullif(p_payload->>'relevance', '')::numeric,
                'sentiment', nullif(p_payload->>'sentiment', '')::numeric,
                'impact', nullif(p_payload->>'impact', '')::numeric),
            'evidence', '{}'::jsonb, 'provenance', '{}'::jsonb
        ));
    END IF;
    RETURN jsonb_build_object('news_id', v_news_id, 'inserted', v_inserted,
        'updated', NOT v_inserted, 'association_inserted', v_association_inserted,
        'associations_inserted', CASE WHEN v_association_inserted THEN 1 ELSE 0 END,
        'content_hash', v_payload->>'content_hash');
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_news_update_entity_scores(p_scores jsonb)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    v_score jsonb;
    v_association public.event_lake%ROWTYPE;
    v_updated integer := 0;
BEGIN
    IF p_scores IS NULL OR jsonb_typeof(p_scores) <> 'array' THEN
        RAISE EXCEPTION 'p_scores must be a JSON array';
    END IF;
    FOR v_score IN SELECT value FROM jsonb_array_elements(p_scores) LOOP
        SELECT * INTO v_association FROM public.event_lake_latest
        WHERE event_type = 'NEWS_ASSOCIATION' AND stage = 'ACQUIRED'
          AND (payload->>'news_entity_id')::uuid = (v_score->>'news_entity_id')::uuid;
        IF NOT FOUND THEN CONTINUE; END IF;
        PERFORM public.sp_save_event(jsonb_build_object(
            'schema_version', 1, 'payload_version', 1, 'event_type', 'NEWS_SCORE',
            'stage', 'ENRICHED', 'source', 'NEWS_ENRICHMENT',
            'event_key', v_score->>'news_entity_id', 'subjects', v_association.subjects,
            'parent_event_ids', jsonb_build_array(v_association.event_id),
            'payload', jsonb_build_object('news_entity_id', v_score->>'news_entity_id',
                'relevance', greatest(0.0, least(1.0, (v_score->>'relevance')::numeric)),
                'sentiment', greatest(-1.0, least(1.0, (v_score->>'sentiment')::numeric)),
                'impact', greatest(0.0, least(1.0, (v_score->>'impact')::numeric))),
            'evidence', '{}'::jsonb, 'provenance', '{}'::jsonb
        ));
        v_updated := v_updated + 1;
    END LOOP;
    RETURN v_updated;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_upsert_catalyst(
    p_entity_id uuid, p_symbol text, p_entity_subtype text,
    p_entity_date timestamptz, p_entity_status text, p_importance text,
    p_title text, p_source text, p_payload jsonb
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_entity_id uuid;
    v_existing public.event_lake%ROWTYPE;
    v_day date;
    v_key text;
BEGIN
    v_entity_id := public.sp_event_resolve_catalyst_entity(p_entity_id, p_symbol);
    v_day := coalesce(nullif(p_payload->>'date', '')::date,
                      (p_entity_date AT TIME ZONE 'UTC')::date);
    SELECT lake.* INTO v_existing
    FROM public.event_lake_latest lake
    WHERE lake.event_type = 'CATALYST' AND lake.stage = 'ACQUIRED'
      AND lake.entity_id = v_entity_id AND lake.source = p_source
      AND lake.event_day = v_day AND lake.payload->>'subtype' = p_entity_subtype
    ORDER BY lake.created_at DESC, lake.event_id LIMIT 1;
    v_key := coalesce(v_existing.event_key,
        encode(sha256(convert_to(jsonb_build_array(
            v_entity_id, p_entity_subtype, v_day)::text, 'UTF8')), 'hex'));
    PERFORM public.sp_save_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', 'CATALYST', 'stage', 'ACQUIRED', 'source', p_source,
        'event_key', v_key, 'occurred_at', p_entity_date, 'event_day', v_day,
        'parent_event_ids', '[]'::jsonb, 'evidence', '{}'::jsonb,
        'subjects', jsonb_build_array(jsonb_build_object(
            'entity_id', v_entity_id, 'symbol', p_symbol)),
        'payload', jsonb_strip_nulls(jsonb_build_object(
            'catalyst_id', v_existing.payload->>'catalyst_id',
            'subtype', p_entity_subtype, 'status', p_entity_status,
            'importance', p_importance, 'title', p_title,
            'details', coalesce(p_payload, '{}'::jsonb))),
        'raw_payload', coalesce(p_payload, '{}'::jsonb),
        'provenance', jsonb_build_object('writer', 'sp_upsert_catalyst')));
    RETURN coalesce((v_existing.payload->>'catalyst_id')::uuid,
        (SELECT event_id FROM public.event_lake
         WHERE event_type = 'CATALYST' AND stage = 'ACQUIRED'
           AND source = p_source AND event_key = v_key
           AND entity_id = v_entity_id AND revision = 1));
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_upsert_filing(
    p_symbol text, p_filing_type text, p_cik text, p_filing_date date,
    p_accepted_at timestamptz, p_title text, p_source text, p_raw_payload jsonb,
    p_filing_subtype text DEFAULT NULL, p_accession_number text DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_entity_id uuid;
    v_existing public.event_lake%ROWTYPE;
    v_key text;
BEGIN
    v_entity_id := public.sp_event_resolve_filing_entity(p_symbol, p_cik);
    SELECT lake.* INTO v_existing FROM public.event_lake_latest lake
    WHERE lake.event_type = 'FILING' AND lake.stage = 'ACQUIRED'
      AND lake.source = p_source AND lake.entity_id = v_entity_id
      AND lake.payload->>'cik' = p_cik
      AND lake.payload->>'filing_type' = p_filing_type
      AND lake.occurred_at = p_accepted_at
    ORDER BY lake.created_at DESC, lake.event_id LIMIT 1;
    v_key := coalesce(v_existing.event_key,
        encode(sha256(convert_to(jsonb_build_array(p_cik,
            coalesce(p_accession_number,
                jsonb_build_array(p_filing_type, p_accepted_at)::text))::text,
            'UTF8')), 'hex'));
    PERFORM public.sp_save_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', 'FILING', 'stage', 'ACQUIRED', 'source', p_source,
        'event_key', v_key, 'source_event_id', p_accession_number,
        'occurred_at', p_accepted_at, 'event_day', p_filing_date,
        'parent_event_ids', '[]'::jsonb, 'evidence', '{}'::jsonb,
        'subjects', jsonb_build_array(jsonb_build_object(
            'entity_id', v_entity_id, 'symbol', p_symbol, 'cik', p_cik)),
        'payload', jsonb_strip_nulls(jsonb_build_object(
            'filing_id', v_existing.payload->>'filing_id',
            'filing_type', p_filing_type, 'filing_subtype', p_filing_subtype,
            'cik', p_cik, 'accepted_at', p_accepted_at,
            'accession_number', p_accession_number, 'title', p_title,
            'created_at', v_existing.payload->>'created_at')),
        'raw_payload', coalesce(p_raw_payload, '{}'::jsonb),
        'provenance', jsonb_build_object('writer', 'sp_upsert_filing')));
    RETURN v_entity_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_save_intelligence_event(
    p_branch text, p_event_key text, p_entity_id uuid, p_instrument_id uuid,
    p_category text, p_payload jsonb, p_evidence jsonb
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_key text;
    v_day date;
BEGIN
    SELECT lake.event_key INTO v_key FROM public.event_lake_latest lake
    WHERE lake.event_type = p_branch AND lake.stage = 'VALIDATED'
      AND lake.source = 'COLLECTION_RESEARCH' AND lake.entity_id = p_entity_id
      AND coalesce(lake.provenance->>'projection_event_key', lake.event_key) = p_event_key
      AND lake.subjects->0->>'instrument_id' = p_instrument_id::text
    ORDER BY lake.created_at DESC, lake.event_id LIMIT 1;
    v_day := CASE WHEN p_branch = 'DAILY_MOVER' AND strpos(p_event_key, '|') > 0
                  THEN split_part(p_event_key, '|', 2)::date
                  ELSE nullif(p_payload->>'eventDate', '')::date END;
    PERFORM public.sp_save_event(jsonb_build_object(
        'schema_version', 1, 'payload_version', 1,
        'event_type', p_branch, 'stage', 'VALIDATED',
        'source', 'COLLECTION_RESEARCH', 'event_key', coalesce(v_key, p_event_key),
        'event_day', v_day,
        'parent_event_ids', '[]'::jsonb,
        'subjects', jsonb_build_array(jsonb_build_object(
            'entity_id', p_entity_id, 'instrument_id', p_instrument_id)),
        'payload', p_payload - ARRAY['daysUntilEvent', 'eventKey', 'sources',
            'relatedSymbols', 'name', 'symbol', 'entity_id', 'observedDate', 'marketEvidence'],
        'raw_payload', p_payload,
        'evidence', coalesce(p_evidence, '{}'::jsonb) - 'marketEvidence',
        'provenance', jsonb_build_object('branch', p_branch,
            'category', p_category, 'projection_event_key', p_event_key,
            'marketEvidence', p_evidence->'marketEvidence')));
END;
$$;

DO $consolidation$
DECLARE
    definition text;
    legacy_block text;
    block_pattern text;
    alias_statement text := '    INSERT INTO entity_aliases (';
BEGIN
    definition := pg_get_functiondef(
        'public.sp_consolidate_entity(uuid,uuid,boolean)'::regprocedure
    );
    FOREACH block_pattern IN ARRAY ARRAY[
        '(?s)    UPDATE (public\.)?catalysts\s.*?(?=    UPDATE (public\.)?etf_constituents\s)',
        '(?s)    UPDATE (public\.)?news_entities\s.*?(?=    UPDATE (public\.)?notifications\s)'
    ] LOOP
        legacy_block := regexp_substr(definition, block_pattern);
        IF legacy_block IS NULL THEN
            RAISE EXCEPTION 'Unexpected entity consolidation body; no cutover applied';
        END IF;
        definition := replace(definition, legacy_block, '');
    END LOOP;
    IF strpos(definition, alias_statement) = 0 THEN
        RAISE EXCEPTION 'Missing entity consolidation guard location; no cutover applied';
    END IF;
    definition := replace(definition, alias_statement, $guard$
    IF EXISTS (
        SELECT FROM public.event_lake
        WHERE entity_id = p_source_entity_id
    ) THEN
        RAISE EXCEPTION
            'Cannot consolidate an entity referenced by immutable lake events; an entity redirect policy is required';
    END IF;

$guard$ || alias_statement);
    IF definition ~* '(UPDATE|INSERT INTO|DELETE FROM)\s+(public\.)?(news|news_entities|news_clusters|news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\M' THEN
        RAISE EXCEPTION 'Entity consolidation still writes legacy archives; no cutover applied';
    END IF;
    EXECUTE definition;
END;
$consolidation$;

DO $verification$
DECLARE
    remaining_writers text;
BEGIN
    SELECT string_agg(routine.oid::regprocedure::text, ', ')
    INTO remaining_writers
    FROM pg_proc routine
    JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = 'public' AND routine.prokind = 'f'
      AND routine.prosrc ~* '(UPDATE|INSERT INTO|DELETE FROM)\s+(public\.)?(news|news_entities|news_clusters|news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\M';
    IF remaining_writers IS NOT NULL THEN
        RAISE EXCEPTION 'Legacy archive writers remain: %', remaining_writers;
    END IF;
    IF EXISTS (
        SELECT FROM pg_depend dependency
        JOIN pg_rewrite rule ON rule.oid = dependency.objid
        JOIN pg_class dependent ON dependent.oid = rule.ev_class
        JOIN pg_class archive ON archive.oid = dependency.refobjid
        JOIN pg_namespace namespace ON namespace.oid = archive.relnamespace
        WHERE namespace.nspname = 'event_legacy'
          AND dependency.refclassid = 'pg_class'::regclass
          AND dependency.classid = 'pg_rewrite'::regclass
          AND dependent.oid <> archive.oid
    ) THEN
        RAISE EXCEPTION 'Views still depend on physical legacy archives; no cutover applied';
    END IF;
END;
$verification$;

COMMIT;