BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_screener_snapshot_changes(
    p_snapshot_id uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE sql STABLE AS $$
WITH current_snapshot AS (
    SELECT s.*
    FROM public.market_screener_ranking_snapshots s
    JOIN public.market_screener_runs r USING (screener_run_id)
    WHERE r.status = 'SUCCESS'
            AND r.phase_results->'analysis_scan'->>'status' = 'SUCCESS'
            AND r.phase_results->'ranking'->>'status' = 'SUCCESS'
      AND (p_snapshot_id IS NULL OR s.ranking_snapshot_id = p_snapshot_id)
    ORDER BY s.observed_at DESC, s.ranking_snapshot_id DESC LIMIT 1
), previous_snapshot AS (
    SELECT s.*
    FROM public.market_screener_ranking_snapshots s
    JOIN public.market_screener_runs r USING (screener_run_id)
    CROSS JOIN current_snapshot current
    WHERE r.status = 'SUCCESS'
            AND r.phase_results->'analysis_scan'->>'status' = 'SUCCESS'
            AND r.phase_results->'ranking'->>'status' = 'SUCCESS'
      AND (s.observed_at, s.ranking_snapshot_id)
        < (current.observed_at, current.ranking_snapshot_id)
    ORDER BY s.observed_at DESC, s.ranking_snapshot_id DESC LIMIT 1
), current_items AS (
    SELECT DISTINCT ON (instrument_id, coalesce(signal_timeframe, '')) item.*
    FROM public.market_screener_ranking_snapshot_items item
    JOIN current_snapshot USING (ranking_snapshot_id)
    ORDER BY instrument_id, coalesce(signal_timeframe, ''), ranking_position, opportunity_id
), previous_items AS (
    SELECT DISTINCT ON (instrument_id, coalesce(signal_timeframe, '')) item.*
    FROM public.market_screener_ranking_snapshot_items item
    JOIN previous_snapshot USING (ranking_snapshot_id)
    ORDER BY instrument_id, coalesce(signal_timeframe, ''), ranking_position, opportunity_id
), changes AS (
    SELECT CASE
        WHEN previous.instrument_id IS NULL THEN 'NEW'
        WHEN current.instrument_id IS NULL THEN 'REMOVED'
        ELSE 'CHANGED' END AS change_type,
        coalesce(current.symbol, previous.symbol) AS symbol,
        coalesce(current.ranking_position, previous.ranking_position) AS ranking_position,
        jsonb_build_object(
            'change_type', CASE WHEN previous.instrument_id IS NULL THEN 'NEW'
                WHEN current.instrument_id IS NULL THEN 'REMOVED' ELSE 'CHANGED' END,
            'current', CASE WHEN current.instrument_id IS NOT NULL THEN to_jsonb(current) END,
            'previous', CASE WHEN previous.instrument_id IS NOT NULL THEN to_jsonb(previous) END
        ) AS payload
    FROM current_items current
    FULL JOIN previous_items previous ON current.instrument_id = previous.instrument_id
        AND coalesce(current.signal_timeframe, '') = coalesce(previous.signal_timeframe, '')
    WHERE current.instrument_id IS NULL OR previous.instrument_id IS NULL
       OR ROW(current.signal_id, current.strategy_code, current.signal_type,
              current.direction, current.consensus_direction, current.verdict,
              current.supporting_strategies, current.opposing_strategies)
          IS DISTINCT FROM
          ROW(previous.signal_id, previous.strategy_code, previous.signal_type,
              previous.direction, previous.consensus_direction, previous.verdict,
              previous.supporting_strategies, previous.opposing_strategies)
)
SELECT jsonb_build_object(
    'ranking_snapshot_id', current.ranking_snapshot_id,
    'previous_snapshot_id', (SELECT ranking_snapshot_id FROM previous_snapshot),
    'observed_at', current.observed_at,
    'opportunity_count', current.opportunity_count,
    'new_count', (SELECT count(*) FROM changes WHERE change_type = 'NEW'),
    'changed_count', (SELECT count(*) FROM changes WHERE change_type = 'CHANGED'),
    'removed_count', (SELECT count(*) FROM changes WHERE change_type = 'REMOVED'),
    'changes', coalesce((SELECT jsonb_agg(payload ORDER BY ranking_position, symbol)
                        FROM changes), '[]'::jsonb)
) FROM current_snapshot current;
$$;

COMMIT;