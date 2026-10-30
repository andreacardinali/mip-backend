BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_screener_enrich_opportunity_payload(p_payload jsonb, p_opportunity_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN p_payload IS NULL THEN NULL
        ELSE p_payload || jsonb_build_object(
            'entity_id', entity.entity_id,
            'entity_name', entity.name,
            'entity_type', entity.entity_type,
            'entity_country_code', entity.country_code,
            'source_instrument_id', instrument.instrument_id,
            'source_symbol', instrument.symbol,
            'source_exchange', instrument.exchange,
            'source_currency_code', instrument.currency_code,
            'source_isin', NULL,
            'last_analysis_at', (
                SELECT max(r.finished_at)
                FROM public.market_analysis_scan_runs r
                WHERE r.status = 'COMPLETED'
                  AND (r.summary -> 'evaluated_instrument_ids') ? instrument.instrument_id::text
            ),
            'strategy_code', opportunity.strategy_code,
            'signal_type', opportunity.signal_type,
            'signal_timeframe', opportunity.signal_timeframe,
            'signal_trend', opportunity.signal_trend,
            'signal_confidence', opportunity.signal_confidence,
            'reference_price', opportunity.reference_price,
            'invalidation_price', opportunity.invalidation_price,
            'target_price', opportunity.target_price,
            'signal_reasons', coalesce(opportunity.signal_reasons, '[]'::jsonb),
            'technical_evidence', coalesce(opportunity.technical_evidence, '{}'::jsonb),
            'portfolio_match', coalesce((p_payload ->> 'portfolio_match')::boolean, false),
            'portfolio_match_type', coalesce(
                p_payload ->> 'portfolio_match_type',
                'NO_MATCH'
            ),
            'portfolio_position_count', coalesce(
                (p_payload ->> 'portfolio_position_count')::integer,
                0
            ),
            'portfolio_symbols', coalesce(
                p_payload -> 'portfolio_symbols',
                '[]'::jsonb
            ),
            'portfolio_names', coalesce(
                p_payload -> 'portfolio_names',
                '[]'::jsonb
            ),
            'portfolio_positions', coalesce(
                p_payload -> 'portfolio_positions',
                '[]'::jsonb
            ),
            'alternative_instruments', coalesce(
                alternatives.items,
                '[]'::jsonb
            )
        )
    END
    FROM public.market_screener_opportunities opportunity
    JOIN public.instruments instrument
      ON instrument.instrument_id = opportunity.instrument_id
    JOIN public.entities entity
      ON entity.entity_id = coalesce(
          opportunity.entity_id,
          instrument.entity_id
      )
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'instrument_id', related.instrument_id,
                'symbol', related.symbol,
                'exchange', related.exchange,
                'currency_code', related.currency_code,
                'isin', NULL,
                'is_source', related.instrument_id = instrument.instrument_id,
                'same_isin', false
            )
            ORDER BY
                (related.instrument_id = instrument.instrument_id) DESC,
                related.symbol,
                related.exchange
        ) AS items
        FROM public.instruments related
        WHERE related.entity_id = entity.entity_id
    ) alternatives ON true
    WHERE opportunity.opportunity_id = p_opportunity_id;
$$;

COMMIT;