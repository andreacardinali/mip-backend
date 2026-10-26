BEGIN;

CREATE TABLE IF NOT EXISTS public.market_discovery_candidates (
    candidate_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES public.market_discovery_snapshots(snapshot_id) ON DELETE CASCADE,
    snapshot_item_id uuid NOT NULL REFERENCES public.market_discovery_snapshot_items(snapshot_item_id) ON DELETE CASCADE,
    entity_id uuid REFERENCES public.entities(entity_id) ON DELETE RESTRICT,
    instrument_id uuid REFERENCES public.instruments(instrument_id) ON DELETE RESTRICT,
    symbol varchar(50) NOT NULL,
    origin varchar(20),
    resolution_status varchar(20) NOT NULL,
    attention_score numeric(7, 4) NOT NULL,
    universe_codes jsonb DEFAULT '[]'::jsonb NOT NULL,
    attention_evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    updated_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT uq_market_discovery_candidates_snapshot_symbol UNIQUE (snapshot_id, symbol),
    CONSTRAINT uq_market_discovery_candidates_snapshot_item UNIQUE (snapshot_item_id),
    CONSTRAINT ck_market_discovery_candidates_symbol CHECK (btrim(symbol) <> ''),
    CONSTRAINT ck_market_discovery_candidates_origin CHECK (
        origin IS NULL OR origin IN ('CORE', 'DISCOVERY')
    ),
    CONSTRAINT ck_market_discovery_candidates_resolution CHECK (
        resolution_status IN ('RESOLVED', 'AMBIGUOUS', 'UNRESOLVED', 'REJECTED')
    ),
    CONSTRAINT ck_market_discovery_candidates_score CHECK (
        attention_score >= 0 AND attention_score <= 100
    ),
    CONSTRAINT ck_market_discovery_candidates_resolution_values CHECK (
        (resolution_status = 'RESOLVED' AND entity_id IS NOT NULL AND instrument_id IS NOT NULL AND origin IS NOT NULL)
        OR
        (resolution_status <> 'RESOLVED' AND origin IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_market_discovery_candidates_rank
    ON public.market_discovery_candidates(snapshot_id, resolution_status, attention_score DESC);
CREATE INDEX IF NOT EXISTS ix_market_discovery_candidates_instrument
    ON public.market_discovery_candidates(instrument_id)
    WHERE instrument_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_market_discovery_candidates_entity
    ON public.market_discovery_candidates(entity_id)
    WHERE entity_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_market_discovery_candidates_created_at
    ON public.market_discovery_candidates;
CREATE TRIGGER trg_market_discovery_candidates_created_at
BEFORE INSERT ON public.market_discovery_candidates
FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();

DROP TRIGGER IF EXISTS trg_market_discovery_candidates_updated_at
    ON public.market_discovery_candidates;
CREATE TRIGGER trg_market_discovery_candidates_updated_at
BEFORE INSERT OR UPDATE ON public.market_discovery_candidates
FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();

CREATE OR REPLACE FUNCTION public.sp_market_discovery_build_candidates(
    p_snapshot_id uuid DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
AS $function$
DECLARE
    v_snapshot_id uuid;
    v_total integer;
    v_resolved integer;
    v_core integer;
    v_discovery integer;
    v_ambiguous integer;
    v_unresolved integer;
    v_rejected integer;
BEGIN
    SELECT COALESCE(
        p_snapshot_id,
        (
            SELECT s.snapshot_id
            FROM public.market_discovery_snapshots s
            ORDER BY s.observed_at DESC, s.snapshot_id DESC
            LIMIT 1
        )
    )
    INTO v_snapshot_id;

    IF v_snapshot_id IS NULL THEN
        RAISE EXCEPTION 'No market discovery snapshot is available';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.market_discovery_snapshots s
        WHERE s.snapshot_id = v_snapshot_id
    ) THEN
        RAISE EXCEPTION 'Market discovery snapshot not found: %', v_snapshot_id;
    END IF;

    DELETE FROM public.market_discovery_candidates c
    WHERE c.snapshot_id = v_snapshot_id;

    WITH snapshot_context AS (
        SELECT s.snapshot_id, GREATEST(s.returned_count, 1) AS returned_count
        FROM public.market_discovery_snapshots s
        WHERE s.snapshot_id = v_snapshot_id
    ),
    item_scores AS (
        SELECT
            i.*,
            LEAST(
                35.0,
                GREATEST(
                    0.0,
                    35.0 * (
                        1.0 - (
                            GREATEST(COALESCE(i.rank, sc.returned_count), 1) - 1
                        )::numeric / GREATEST(sc.returned_count - 1, 1)
                    )
                )
            ) AS rank_score,
            LEAST(
                30.0,
                15.0 * LEAST(
                    1.0,
                    LN(1 + GREATEST(COALESCE(i.mentions, 0) - COALESCE(i.previous_mentions, 0), 0)) / LN(51.0)
                )
                + 15.0 * LEAST(
                    1.0,
                    GREATEST(COALESCE(i.mentions, 0) - COALESCE(i.previous_mentions, 0), 0)::numeric
                    / GREATEST(COALESCE(i.previous_mentions, 0), 1)
                )
            ) AS mentions_growth_score,
            LEAST(
                20.0,
                20.0 * LEAST(
                    1.0,
                    LN(
                        1 + GREATEST(
                            COALESCE(i.previous_rank, i.rank) - COALESCE(i.rank, i.previous_rank),
                            0
                        )
                    ) / LN(101.0)
                )
            ) AS rank_momentum_score,
            LEAST(
                15.0,
                15.0 * LEAST(
                    1.0,
                    LN(1 + GREATEST(COALESCE(i.upvotes, 0), 0)) / LN(1001.0)
                )
            ) AS engagement_score
        FROM public.market_discovery_snapshot_items i
        JOIN snapshot_context sc ON sc.snapshot_id = i.snapshot_id
    ),
    exact_matches AS (
        SELECT
            scores.snapshot_item_id,
            i.instrument_id,
            i.entity_id,
            e.active AS instrument_active,
            EXISTS (
                SELECT 1
                FROM public.market_universe_members um
                JOIN public.market_universes u
                  ON u.universe_id = um.universe_id
                WHERE um.instrument_id = i.instrument_id
                  AND um.valid_to IS NULL
                  AND u.active = true
            ) AS is_core_listing
        FROM item_scores scores
        JOIN public.instruments i
          ON upper(btrim(i.symbol)) =
             upper(btrim(scores.symbol))
        JOIN public.entities e
          ON e.entity_id = i.entity_id
    ),
    match_summary AS (
        SELECT
            scores.snapshot_item_id,
            COUNT(matches.instrument_id) AS all_instrument_count,
            COUNT(matches.instrument_id) FILTER (WHERE matches.instrument_active) AS active_instrument_count,
            COUNT(DISTINCT matches.entity_id) FILTER (WHERE matches.instrument_active) AS active_entity_count
        FROM item_scores scores
        LEFT JOIN exact_matches matches ON matches.snapshot_item_id = scores.snapshot_item_id
        GROUP BY scores.snapshot_item_id
    ),
    selected_match AS (
        SELECT DISTINCT ON (matches.snapshot_item_id)
            matches.snapshot_item_id,
            matches.instrument_id,
            matches.entity_id,
            matches.is_core_listing
        FROM exact_matches matches
        JOIN match_summary summary ON summary.snapshot_item_id = matches.snapshot_item_id
        WHERE matches.instrument_active
          AND summary.active_entity_count = 1
        ORDER BY
            matches.snapshot_item_id,
            matches.is_core_listing DESC,
            matches.instrument_id
    ),
    universe_memberships AS (
        SELECT
            selected.snapshot_item_id,
            COALESCE(
                jsonb_agg(DISTINCT u.code ORDER BY u.code)
                    FILTER (WHERE u.code IS NOT NULL),
                '[]'::jsonb
            ) AS universe_codes
        FROM selected_match selected
        LEFT JOIN public.market_universe_members um
          ON um.instrument_id = selected.instrument_id
         AND um.valid_to IS NULL
        LEFT JOIN public.market_universes u
          ON u.universe_id = um.universe_id
         AND u.active = true
        GROUP BY selected.snapshot_item_id
    )
    INSERT INTO public.market_discovery_candidates (
        snapshot_id,
        snapshot_item_id,
        entity_id,
        instrument_id,
        symbol,
        origin,
        resolution_status,
        attention_score,
        universe_codes,
        attention_evidence
    )
    SELECT
        scores.snapshot_id,
        scores.snapshot_item_id,
        CASE WHEN summary.active_entity_count = 1 THEN selected.entity_id END,
        CASE WHEN summary.active_entity_count = 1 THEN selected.instrument_id END,
        upper(btrim(scores.symbol)),
        CASE
            WHEN summary.active_entity_count = 1 AND COALESCE(jsonb_array_length(memberships.universe_codes), 0) > 0
                THEN 'CORE'
            WHEN summary.active_entity_count = 1
                THEN 'DISCOVERY'
            ELSE NULL
        END,
        CASE
            WHEN summary.all_instrument_count = 0 THEN 'UNRESOLVED'
            WHEN summary.active_instrument_count = 0 THEN 'REJECTED'
            WHEN summary.active_entity_count > 1 THEN 'AMBIGUOUS'
            WHEN summary.active_entity_count = 1 THEN 'RESOLVED'
            ELSE 'UNRESOLVED'
        END,
        ROUND(
            (
                LEAST(
                    100.0,
                    GREATEST(
                        0.0,
                        scores.rank_score
                        + scores.mentions_growth_score
                        + scores.rank_momentum_score
                        + scores.engagement_score
                    )
                )
            )::numeric,
            4
        ),
        COALESCE(memberships.universe_codes, '[]'::jsonb),
        jsonb_build_object(
            'rank', scores.rank,
            'previous_rank', scores.previous_rank,
            'rank_improvement', CASE
                WHEN scores.rank IS NOT NULL AND scores.previous_rank IS NOT NULL
                    THEN scores.previous_rank - scores.rank
                ELSE NULL
            END,
            'mentions', scores.mentions,
            'previous_mentions', scores.previous_mentions,
            'mentions_delta', COALESCE(scores.mentions, 0) - COALESCE(scores.previous_mentions, 0),
            'upvotes', scores.upvotes,
            'components', jsonb_build_object(
                'rank_score',
                    ROUND(scores.rank_score::numeric, 4),
                'mentions_growth_score',
                    ROUND(scores.mentions_growth_score::numeric, 4),
                'rank_momentum_score',
                    ROUND(scores.rank_momentum_score::numeric, 4),
                'engagement_score',
                    ROUND(scores.engagement_score::numeric, 4)
            ),
            'resolution', jsonb_build_object(
                'all_instrument_count', summary.all_instrument_count,
                'active_instrument_count', summary.active_instrument_count,
                'active_entity_count', summary.active_entity_count,
                'selected_instrument_id', selected.instrument_id,
                'selected_entity_id', selected.entity_id,
                'selection_rule', 'exact symbol; unique active entity; active universe listing preferred; instrument_id tie-break'
            )
        )
    FROM item_scores scores
    JOIN match_summary summary ON summary.snapshot_item_id = scores.snapshot_item_id
    LEFT JOIN selected_match selected ON selected.snapshot_item_id = scores.snapshot_item_id
    LEFT JOIN universe_memberships memberships ON memberships.snapshot_item_id = scores.snapshot_item_id;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE resolution_status = 'RESOLVED'),
        COUNT(*) FILTER (WHERE origin = 'CORE'),
        COUNT(*) FILTER (WHERE origin = 'DISCOVERY'),
        COUNT(*) FILTER (WHERE resolution_status = 'AMBIGUOUS'),
        COUNT(*) FILTER (WHERE resolution_status = 'UNRESOLVED'),
        COUNT(*) FILTER (WHERE resolution_status = 'REJECTED')
    INTO
        v_total,
        v_resolved,
        v_core,
        v_discovery,
        v_ambiguous,
        v_unresolved,
        v_rejected
    FROM public.market_discovery_candidates c
    WHERE c.snapshot_id = v_snapshot_id;

    RETURN jsonb_build_object(
        'snapshot_id', v_snapshot_id,
        'total', v_total,
        'resolved', v_resolved,
        'core', v_core,
        'discovery', v_discovery,
        'ambiguous', v_ambiguous,
        'unresolved', v_unresolved,
        'rejected', v_rejected
    );
END;
$function$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_list_candidates(
    p_snapshot_id uuid DEFAULT NULL,
    p_resolution_status varchar DEFAULT NULL,
    p_origin varchar DEFAULT NULL,
    p_min_attention_score numeric DEFAULT 0,
    p_limit integer DEFAULT 100
) RETURNS TABLE (
    candidate_id uuid,
    snapshot_id uuid,
    snapshot_item_id uuid,
    entity_id uuid,
    instrument_id uuid,
    symbol varchar,
    origin varchar,
    resolution_status varchar,
    attention_score numeric,
    universe_codes jsonb,
    attention_evidence jsonb,
    observed_at timestamptz
)
LANGUAGE sql
STABLE
AS $function$
WITH selected_snapshot AS (
    SELECT COALESCE(
        p_snapshot_id,
        (
            SELECT s.snapshot_id
            FROM public.market_discovery_snapshots s
            ORDER BY s.observed_at DESC, s.snapshot_id DESC
            LIMIT 1
        )
    ) AS snapshot_id
)
SELECT
    c.candidate_id,
    c.snapshot_id,
    c.snapshot_item_id,
    c.entity_id,
    c.instrument_id,
    c.symbol,
    c.origin,
    c.resolution_status,
    c.attention_score,
    c.universe_codes,
    c.attention_evidence,
    s.observed_at
FROM public.market_discovery_candidates c
JOIN selected_snapshot selected ON selected.snapshot_id = c.snapshot_id
JOIN public.market_discovery_snapshots s ON s.snapshot_id = c.snapshot_id
WHERE (p_resolution_status IS NULL OR c.resolution_status = upper(btrim(p_resolution_status)))
  AND (p_origin IS NULL OR c.origin = upper(btrim(p_origin)))
  AND c.attention_score >= COALESCE(p_min_attention_score, 0)
ORDER BY c.attention_score DESC, c.symbol
LIMIT LEAST(GREATEST(p_limit, 1), 1000);
$function$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_get_candidate(
    p_candidate_id uuid
) RETURNS jsonb
LANGUAGE sql
STABLE
AS $function$
SELECT jsonb_build_object(
    'candidate_id', c.candidate_id,
    'snapshot_id', c.snapshot_id,
    'snapshot_item_id', c.snapshot_item_id,
    'entity_id', c.entity_id,
    'instrument_id', c.instrument_id,
    'symbol', c.symbol,
    'origin', c.origin,
    'resolution_status', c.resolution_status,
    'attention_score', c.attention_score,
    'universe_codes', c.universe_codes,
    'attention_evidence', c.attention_evidence,
    'created_at', c.created_at,
    'updated_at', c.updated_at
)
FROM public.market_discovery_candidates c
WHERE c.candidate_id = p_candidate_id;
$function$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_delete_expired_candidates(
    p_retention_days integer
) RETURNS integer
LANGUAGE plpgsql
AS $function$
DECLARE
    v_deleted integer;
BEGIN
    IF p_retention_days < 1 THEN
        RAISE EXCEPTION 'retention_days must be positive';
    END IF;

    DELETE FROM public.market_discovery_candidates c
    USING public.market_discovery_snapshots s
    WHERE s.snapshot_id = c.snapshot_id
      AND s.observed_at < now() - make_interval(days => p_retention_days);

    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$function$;

COMMIT;
