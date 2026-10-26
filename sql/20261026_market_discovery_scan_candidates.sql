BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_list_scan_candidates(
    p_snapshot_id uuid DEFAULT NULL,
    p_core_min_attention_score numeric DEFAULT 20,
    p_discovery_min_attention_score numeric DEFAULT 40,
    p_core_limit integer DEFAULT 100,
    p_discovery_limit integer DEFAULT 25
) RETURNS TABLE (
    candidate_id uuid,
    snapshot_id uuid,
    instrument_id uuid,
    entity_id uuid,
    symbol varchar,
    exchange text,
    currency_code text,
    isin text,
    universe_id uuid,
    origin varchar,
    attention_score numeric,
    universe_codes jsonb,
    attention_evidence jsonb
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
),
eligible AS (
    SELECT
        c.*,
        ROW_NUMBER() OVER (
            PARTITION BY c.origin
            ORDER BY c.attention_score DESC, c.symbol
        ) AS origin_position
    FROM public.market_discovery_candidates c
    JOIN selected_snapshot selected
      ON selected.snapshot_id = c.snapshot_id
    WHERE c.resolution_status = 'RESOLVED'
      AND (
          (c.origin = 'CORE' AND c.attention_score >= COALESCE(p_core_min_attention_score, 0))
          OR
          (c.origin = 'DISCOVERY' AND c.attention_score >= COALESCE(p_discovery_min_attention_score, 0))
      )
),
limited AS (
    SELECT *
    FROM eligible
    WHERE (origin = 'CORE' AND origin_position <= GREATEST(p_core_limit, 0))
       OR (origin = 'DISCOVERY' AND origin_position <= GREATEST(p_discovery_limit, 0))
),
deduplicated AS (
    SELECT DISTINCT ON (instrument_id)
        *
    FROM limited
    ORDER BY
        instrument_id,
        CASE origin WHEN 'CORE' THEN 0 ELSE 1 END,
        attention_score DESC,
        symbol
),
preferred_universe AS (
    SELECT
        d.candidate_id,
        membership.universe_id
    FROM deduplicated d
    LEFT JOIN LATERAL (
        SELECT um.universe_id
        FROM public.market_universe_members um
        JOIN public.market_universes u
          ON u.universe_id = um.universe_id
        WHERE um.instrument_id = d.instrument_id
          AND um.valid_to IS NULL
          AND u.active = true
        ORDER BY u.code, um.universe_id
        LIMIT 1
    ) membership ON true
)
SELECT
    d.candidate_id,
    d.snapshot_id,
    d.instrument_id,
    d.entity_id,
    d.symbol,
    i.exchange,
    i.currency_code,
    NULL::text AS isin,
    preferred.universe_id,
    d.origin,
    d.attention_score,
    d.universe_codes,
    d.attention_evidence
FROM deduplicated d
JOIN public.instruments i
  ON i.instrument_id = d.instrument_id
LEFT JOIN preferred_universe preferred
  ON preferred.candidate_id = d.candidate_id
ORDER BY
    CASE d.origin WHEN 'CORE' THEN 0 ELSE 1 END,
    d.attention_score DESC,
    d.symbol;
$function$;

COMMIT;
