BEGIN;

CREATE OR REPLACE FUNCTION public.sp_get_user_entity_overview(p_user_id uuid, p_entity_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'entity', to_jsonb(e),
        'asset_match', public.sp_get_user_entity_asset_match(p_user_id, e.entity_id),
        'instruments', coalesce((
            SELECT jsonb_agg(to_jsonb(i) ORDER BY i.symbol, i.exchange, i.instrument_id)
            FROM public.instruments i WHERE i.entity_id = e.entity_id
        ), '[]'::jsonb),
        'universes', coalesce((
            SELECT jsonb_agg(to_jsonb(u) ORDER BY u.name, u.universe_id)
            FROM public.market_universes u WHERE u.active AND EXISTS (
                SELECT 1 FROM public.market_universe_members m
                JOIN public.instruments i ON i.instrument_id = m.instrument_id
                WHERE m.universe_id = u.universe_id AND i.entity_id = e.entity_id
                  AND m.valid_to IS NULL
            )
        ), '[]'::jsonb),
        'news_count', (SELECT count(*) FROM ( SELECT DISTINCT ON (((payload ->> 'news_id'::text)::uuid)) (payload ->> 'news_id'::text)::uuid AS news_id,
    (payload ->> 'cluster_id'::text)::uuid AS cluster_id,
    (payload ->> 'source_id'::text)::uuid AS source_id,
    payload ->> 'headline'::text AS title,
    payload ->> 'summary'::text AS summary,
    payload ->> 'url'::text AS url,
    ((payload ->> 'content_hash'::text))::character varying(64) AS content_hash,
    occurred_at AS published_at,
    raw_payload AS raw_json
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) AS event_lake_latest
  WHERE event_type = 'NEWS_ARTICLE'::text AND stage = 'ACQUIRED'::text
  ORDER BY ((payload ->> 'news_id'::text)::uuid), created_at DESC, event_id) n WHERE EXISTS (
            SELECT 1 FROM ( SELECT (association.payload ->> 'news_entity_id'::text)::uuid AS news_entity_id,
    (association.payload ->> 'news_id'::text)::uuid AS news_id,
    association.entity_id,
    (COALESCE(score.payload, association.payload) ->> 'relevance'::text)::numeric AS relevance,
    (COALESCE(score.payload, association.payload) ->> 'sentiment'::text)::numeric AS sentiment,
    (COALESCE(score.payload, association.payload) ->> 'impact'::text)::numeric AS impact
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) association
     LEFT JOIN (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) score ON score.event_type = 'NEWS_SCORE'::text AND score.stage = 'ENRICHED'::text AND score.source = 'NEWS_ENRICHMENT'::text AND score.entity_id = association.entity_id AND score.event_key = (association.payload ->> 'news_entity_id'::text)
  WHERE association.event_type = 'NEWS_ASSOCIATION'::text AND association.stage = 'ACQUIRED'::text) ne
            WHERE ne.news_id = n.news_id AND ne.entity_id = e.entity_id
        )),
        'news', coalesce((SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.published_at DESC NULLS LAST, recent.news_id)
            FROM (SELECT n.news_id, n.title, n.summary, n.url, n.published_at
                FROM ( SELECT DISTINCT ON (((payload ->> 'news_id'::text)::uuid)) (payload ->> 'news_id'::text)::uuid AS news_id,
    (payload ->> 'cluster_id'::text)::uuid AS cluster_id,
    (payload ->> 'source_id'::text)::uuid AS source_id,
    payload ->> 'headline'::text AS title,
    payload ->> 'summary'::text AS summary,
    payload ->> 'url'::text AS url,
    ((payload ->> 'content_hash'::text))::character varying(64) AS content_hash,
    occurred_at AS published_at,
    raw_payload AS raw_json
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) AS event_lake_latest
  WHERE event_type = 'NEWS_ARTICLE'::text AND stage = 'ACQUIRED'::text
  ORDER BY ((payload ->> 'news_id'::text)::uuid), created_at DESC, event_id) n WHERE EXISTS (SELECT 1 FROM ( SELECT (association.payload ->> 'news_entity_id'::text)::uuid AS news_entity_id,
    (association.payload ->> 'news_id'::text)::uuid AS news_id,
    association.entity_id,
    (COALESCE(score.payload, association.payload) ->> 'relevance'::text)::numeric AS relevance,
    (COALESCE(score.payload, association.payload) ->> 'sentiment'::text)::numeric AS sentiment,
    (COALESCE(score.payload, association.payload) ->> 'impact'::text)::numeric AS impact
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) association
     LEFT JOIN (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) score ON score.event_type = 'NEWS_SCORE'::text AND score.stage = 'ENRICHED'::text AND score.source = 'NEWS_ENRICHMENT'::text AND score.entity_id = association.entity_id AND score.event_key = (association.payload ->> 'news_entity_id'::text)
  WHERE association.event_type = 'NEWS_ASSOCIATION'::text AND association.stage = 'ACQUIRED'::text) ne
                    WHERE ne.news_id = n.news_id AND ne.entity_id = e.entity_id)
                ORDER BY n.published_at DESC NULLS LAST, n.news_id LIMIT 50) recent
        ), '[]'::jsonb),
        'catalyst_count', (SELECT count(*) FROM ( SELECT COALESCE((lake.payload ->> 'catalyst_id'::text)::uuid, root.event_id) AS catalyst_id,
    lake.entity_id,
        CASE upper(lake.payload ->> 'subtype'::text)
            WHEN 'QUARTERLY_EARNINGS'::text THEN 'EARNINGS'::text
            WHEN 'CASH_DIVIDEND'::text THEN 'DIVIDEND'::text
            WHEN 'STOCK_SPLIT'::text THEN 'SPLIT'::text
            ELSE upper(lake.payload ->> 'subtype'::text)
        END::character varying(50) AS catalyst_type,
    lake.occurred_at AS catalyst_date,
    ((lake.payload ->> 'status'::text))::character varying(30) AS status,
        CASE upper(lake.payload ->> 'importance'::text)
            WHEN 'HIGH'::text THEN 90::numeric
            WHEN 'MEDIUM'::text THEN 50::numeric
            WHEN 'LOW'::text THEN 10::numeric
            ELSE (lake.payload ->> 'importance'::text)::numeric
        END AS importance,
    lake.payload ->> 'title'::text AS title,
    lake.source::character varying(50) AS source,
    lake.raw_payload,
    lake.event_day AS catalyst_day
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) lake
     JOIN public.event_lake root ON root.event_type = lake.event_type AND root.stage = lake.stage AND root.source = lake.source AND root.event_key = lake.event_key AND root.revision = 1 AND NOT root.entity_id IS DISTINCT FROM lake.entity_id
  WHERE lake.event_type = 'CATALYST'::text AND lake.stage = 'ACQUIRED'::text) c WHERE c.entity_id = e.entity_id),
        'catalysts', coalesce((SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.catalyst_day DESC, recent.catalyst_id)
            FROM (SELECT c.catalyst_id, c.title, c.catalyst_type, c.catalyst_date, c.catalyst_day, c.status, c.importance, c.source
                FROM ( SELECT COALESCE((lake.payload ->> 'catalyst_id'::text)::uuid, root.event_id) AS catalyst_id,
    lake.entity_id,
        CASE upper(lake.payload ->> 'subtype'::text)
            WHEN 'QUARTERLY_EARNINGS'::text THEN 'EARNINGS'::text
            WHEN 'CASH_DIVIDEND'::text THEN 'DIVIDEND'::text
            WHEN 'STOCK_SPLIT'::text THEN 'SPLIT'::text
            ELSE upper(lake.payload ->> 'subtype'::text)
        END::character varying(50) AS catalyst_type,
    lake.occurred_at AS catalyst_date,
    ((lake.payload ->> 'status'::text))::character varying(30) AS status,
        CASE upper(lake.payload ->> 'importance'::text)
            WHEN 'HIGH'::text THEN 90::numeric
            WHEN 'MEDIUM'::text THEN 50::numeric
            WHEN 'LOW'::text THEN 10::numeric
            ELSE (lake.payload ->> 'importance'::text)::numeric
        END AS importance,
    lake.payload ->> 'title'::text AS title,
    lake.source::character varying(50) AS source,
    lake.raw_payload,
    lake.event_day AS catalyst_day
   FROM (SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) lake.event_id, lake.schema_version, lake.event_type, lake.payload_version, lake.stage, lake.source, lake.source_event_id, lake.event_key, lake.revision, lake.supersedes_event_id, lake.content_hash, lake.occurred_at, lake.event_day, lake.observed_at, lake.ingested_at, lake.subjects, lake.parent_event_ids, lake.payload, lake.raw_payload, lake.evidence, lake.provenance, lake.created_at, lake.updated_at, lake.last_seen_at, lake.entity_id FROM public.event_lake AS lake ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC) lake
     JOIN public.event_lake root ON root.event_type = lake.event_type AND root.stage = lake.stage AND root.source = lake.source AND root.event_key = lake.event_key AND root.revision = 1 AND NOT root.entity_id IS DISTINCT FROM lake.entity_id
  WHERE lake.event_type = 'CATALYST'::text AND lake.stage = 'ACQUIRED'::text) c WHERE c.entity_id = e.entity_id
                ORDER BY c.catalyst_day DESC, c.catalyst_id LIMIT 50) recent
        ), '[]'::jsonb),
        'opportunities', coalesce((SELECT jsonb_agg(
            public.sp_market_screener_enrich_consensus_payload(
                public.sp_market_screener_enrich_opportunity_payload(
                    public.sp_market_screener_get_user_opportunity(p_user_id, o.opportunity_id),
                    o.opportunity_id), o.opportunity_id)
            ORDER BY o.ranking_position, o.opportunity_id)
            FROM public.market_screener_opportunities o WHERE o.entity_id = e.entity_id AND o.active
        ), '[]'::jsonb),
        'last_analysis_at', (
            SELECT max(r.finished_at)
            FROM public.market_analysis_scan_runs r
            WHERE r.status = 'COMPLETED' AND EXISTS (
                SELECT 1 FROM public.instruments i
                WHERE i.entity_id = e.entity_id
                  AND (r.summary -> 'evaluated_instrument_ids') ? i.instrument_id::text
            )
        ),
        'observed_at', statement_timestamp()
    ) FROM public.entities e WHERE e.entity_id = p_entity_id;
$$;

COMMIT;