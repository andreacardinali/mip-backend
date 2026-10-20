BEGIN;

DROP FUNCTION IF EXISTS sp_get_portfolio_summary(uuid, uuid);

CREATE FUNCTION sp_get_portfolio_summary(
    p_portfolio_id uuid,
    p_user_id uuid
)
RETURNS TABLE(
    portfolio_currency char(3),
    reporting_currency char(3),
    invested_value_native numeric,
    portfolio_value_native numeric,
    profit_loss_native numeric,
    invested_value_reporting numeric,
    portfolio_value_reporting numeric,
    profit_loss_reporting numeric,
    profit_loss_pct numeric,
    positions_count bigint
)
LANGUAGE sql
STABLE
AS $$
WITH portfolio_data AS (
    SELECT
        pf.base_currency,
        u.reporting_currency,
        COUNT(pos.position_id) AS positions_count,
        COALESCE(SUM(
            CASE
                WHEN pos.position_id IS NULL OR pos.avg_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * pos.avg_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS invested_value_native,
        COALESCE(SUM(
            CASE
                WHEN pos.position_id IS NULL OR i.last_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * i.last_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS portfolio_value_native
    FROM portfolios pf
    JOIN users u
      ON u.user_id = pf.user_id
    LEFT JOIN positions pos
      ON pos.portfolio_id = pf.portfolio_id
    LEFT JOIN instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE pf.portfolio_id = p_portfolio_id
      AND pf.user_id = p_user_id
    GROUP BY pf.base_currency, u.reporting_currency
), totals AS (
    SELECT
        base_currency,
        reporting_currency,
        invested_value_native,
        portfolio_value_native,
        portfolio_value_native - invested_value_native AS profit_loss_native,
        positions_count
    FROM portfolio_data
)
SELECT
    base_currency,
    reporting_currency,
    invested_value_native,
    portfolio_value_native,
    profit_loss_native,
    fx_convert(invested_value_native, base_currency, reporting_currency),
    fx_convert(portfolio_value_native, base_currency, reporting_currency),
    fx_convert(profit_loss_native, base_currency, reporting_currency),
    CASE
        WHEN invested_value_native > 0
        THEN (profit_loss_native / invested_value_native) * 100
        ELSE NULL
    END,
    positions_count
FROM totals;
$$;

DROP FUNCTION IF EXISTS sp_get_portfolios(uuid);

CREATE FUNCTION sp_get_portfolios(p_user_id uuid)
RETURNS TABLE(
    portfolio_id uuid,
    user_id uuid,
    name varchar,
    portfolio_type varchar,
    description text,
    base_currency char(3),
    invested_value numeric,
    portfolio_value numeric,
    profit_loss numeric,
    profit_loss_pct numeric,
    positions_count bigint,
    created_at timestamptz,
    updated_at timestamptz
)
LANGUAGE sql
STABLE
AS $$
SELECT
    pf.portfolio_id,
    pf.user_id,
    pf.name,
    pf.portfolio_type::varchar,
    pf.description,
    pf.base_currency,
    totals.invested_value,
    totals.portfolio_value,
    totals.portfolio_value - totals.invested_value AS profit_loss,
    CASE
        WHEN totals.invested_value > 0
        THEN ((totals.portfolio_value - totals.invested_value)
              / totals.invested_value) * 100
        ELSE NULL
    END AS profit_loss_pct,
    totals.positions_count,
    pf.created_at,
    pf.updated_at
FROM portfolios pf
LEFT JOIN LATERAL (
    SELECT
        COUNT(pos.position_id) AS positions_count,
        COALESCE(SUM(
            CASE
                WHEN pos.avg_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * pos.avg_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS invested_value,
        COALESCE(SUM(
            CASE
                WHEN i.last_price IS NULL THEN 0
                ELSE fx_convert(
                    pos.quantity * i.last_price,
                    i.currency_code,
                    pf.base_currency
                )
            END
        ), 0)::numeric AS portfolio_value
    FROM positions pos
    JOIN instruments i
      ON i.instrument_id = pos.instrument_id
    WHERE pos.portfolio_id = pf.portfolio_id
) totals ON true
WHERE pf.user_id = p_user_id
ORDER BY pf.name;
$$;

INSERT INTO schema_version(version)
VALUES ('016-portfolio-pnl')
ON CONFLICT (version) DO NOTHING;

COMMIT;
