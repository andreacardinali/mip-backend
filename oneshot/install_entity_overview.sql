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
        'news_count', (SELECT count(*) FROM public.news n WHERE EXISTS (
            SELECT 1 FROM public.news_entities ne
            WHERE ne.news_id = n.news_id AND ne.entity_id = e.entity_id
        )),
        'news', coalesce((SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.published_at DESC NULLS LAST, recent.news_id)
            FROM (SELECT n.news_id, n.title, n.summary, n.url, n.published_at
                FROM public.news n WHERE EXISTS (SELECT 1 FROM public.news_entities ne
                    WHERE ne.news_id = n.news_id AND ne.entity_id = e.entity_id)
                ORDER BY n.published_at DESC NULLS LAST, n.news_id LIMIT 50) recent
        ), '[]'::jsonb),
        'catalyst_count', (SELECT count(*) FROM public.catalysts c WHERE c.entity_id = e.entity_id),
        'catalysts', coalesce((SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.catalyst_day DESC, recent.catalyst_id)
            FROM (SELECT c.catalyst_id, c.title, c.catalyst_type, c.catalyst_date, c.catalyst_day, c.status, c.importance, c.source
                FROM public.catalysts c WHERE c.entity_id = e.entity_id
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
        'observed_at', statement_timestamp()
    ) FROM public.entities e WHERE e.entity_id = p_entity_id;
$$;

COMMIT;