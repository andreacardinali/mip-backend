BEGIN;
SET LOCAL lock_timeout = '10s';

DO $revision$
DECLARE
    definition text;
    legacy_lookup text := $legacy$    SELECT news_entity_id INTO v_news_entity_id FROM public.news_entities
    WHERE news_id = v_news_id AND entity_id = p_entity_id;$legacy$;
    lake_lookup text := $lake$    SELECT (association.payload->>'news_entity_id')::uuid INTO v_news_entity_id
    FROM public.event_lake_latest association
    WHERE association.event_type = 'NEWS_ASSOCIATION' AND association.stage = 'ACQUIRED'
      AND (association.payload->>'news_id')::uuid = v_news_id
      AND association.entity_id = p_entity_id;$lake$;
    signature text;
BEGIN
    IF to_regclass('public.event_lake_latest') IS NULL
       OR to_regprocedure('public.sp_save_event(jsonb)') IS NULL THEN
        RAISE EXCEPTION 'An installed event lake cutover is required';
    END IF;

    legacy_lookup := replace(legacy_lookup, chr(13) || chr(10), chr(10));
    lake_lookup := replace(lake_lookup, chr(13) || chr(10), chr(10));
    definition := replace(
        pg_get_functiondef('public.sp_news_upsert_article(uuid,jsonb)'::regprocedure),
        chr(13) || chr(10), chr(10)
    );
    IF strpos(definition, legacy_lookup) > 0 THEN
        EXECUTE replace(definition, legacy_lookup, lake_lookup);
    ELSIF strpos(definition, lake_lookup) = 0 THEN
        RAISE EXCEPTION 'Unexpected news association lookup; no revision applied';
    END IF;

    FOREACH signature IN ARRAY ARRAY[
        'public.sp_news_upsert_article(uuid,jsonb)',
        'public.sp_news_update_entity_scores(jsonb)',
        'public.sp_upsert_catalyst(uuid,text,text,timestamptz,text,text,text,text,jsonb)',
        'public.sp_upsert_filing(text,text,text,date,timestamptz,text,text,jsonb,text,text)',
        'public.sp_save_intelligence_event(text,text,uuid,uuid,text,jsonb,jsonb)'
    ] LOOP
        definition := pg_get_functiondef(signature::regprocedure);
        IF strpos(definition, 'public.sp_save_event(') = 0 THEN
            RAISE EXCEPTION 'Writer % does not delegate to sp_save_event', signature;
        END IF;
        IF definition ~* '\m(FROM|JOIN|UPDATE|INTO|DELETE FROM)\s+(public\.)?(news|news_entities|news_clusters|news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\M' THEN
            RAISE EXCEPTION 'Writer % still references legacy projections', signature;
        END IF;
        IF definition ~* '\m(UPDATE|INSERT INTO|DELETE FROM)\s+(public\.)?event_lake\M' THEN
            RAISE EXCEPTION 'Writer % bypasses sp_save_event', signature;
        END IF;
    END LOOP;
END;
$revision$;

COMMIT;