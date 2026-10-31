BEGIN;

SET LOCAL TIME ZONE 'UTC';

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
    SELECT news_entity_id INTO v_news_entity_id FROM public.news_entities
    WHERE news_id = v_news_id AND entity_id = p_entity_id;
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

COMMIT;