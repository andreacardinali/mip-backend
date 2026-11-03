BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE public.collection_intelligence_events IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    current_definition text;
    legacy_row_count bigint;
    contract_count integer;
BEGIN
    IF to_regclass('public.event_lake_latest') IS NULL
       OR to_regprocedure('public.sp_save_event(jsonb)') IS NULL
       OR to_regprocedure(
           'public.sp_save_intelligence_event(text,text,uuid,uuid,text,jsonb,jsonb)'
       ) IS NULL THEN
        RAISE EXCEPTION 'The event lake and intelligence writer must already exist';
    END IF;

    SELECT count(*) INTO legacy_row_count
    FROM public.collection_intelligence_events;
    IF legacy_row_count <> 0 THEN
        RAISE EXCEPTION
            'Legacy intelligence table contains % rows; reconcile them before writer cutover',
            legacy_row_count;
    END IF;

    SELECT count(DISTINCT event_type) INTO contract_count
    FROM public.event_type_catalog
    WHERE event_type = ANY(ARRAY[
        'DAILY_MOVER', 'UPCOMING_CATALYST', 'BREAKING_CATALYST'
    ])
      AND payload_version = 1
      AND 'VALIDATED' = ANY(allowed_stages);
    IF contract_count <> 3 THEN
        RAISE EXCEPTION
            'Expected VALIDATED payload-version-1 contracts for all intelligence branches';
    END IF;

    current_definition := pg_get_functiondef(
        'public.sp_save_intelligence_event(text,text,uuid,uuid,text,jsonb,jsonb)'::regprocedure
    );
    IF NOT (
        (
            current_definition ~* 'INSERT INTO public\.collection_intelligence_events'
            AND strpos(current_definition, 'public.sp_save_event(') = 0
        )
        OR
        (
            strpos(current_definition, 'public.sp_save_event(') > 0
            AND current_definition !~* '\m(FROM|JOIN|UPDATE|INTO|DELETE FROM)\s+(public\.)?collection_intelligence_events\M'
        )
    ) THEN
        RAISE EXCEPTION
            'Unexpected intelligence writer definition; inspect it before applying this repair';
    END IF;
END;
$preflight$;

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

DO $verification$
DECLARE
    definition text := pg_get_functiondef(
        'public.sp_save_intelligence_event(text,text,uuid,uuid,text,jsonb,jsonb)'::regprocedure
    );
BEGIN
    IF strpos(definition, 'public.sp_save_event(') = 0
       OR definition ~* '\m(FROM|JOIN|UPDATE|INTO|DELETE FROM)\s+(public\.)?collection_intelligence_events\M' THEN
        RAISE EXCEPTION 'Intelligence writer verification failed; transaction will roll back';
    END IF;
END;
$verification$;

COMMIT;