# MIP entity performance with historical FX

This package adds database support for comparing all listings of one MIP entity on a common reporting-currency basis.

## Expected behavior

Listings tied to the same ISIN represent the same security. Their normalized total direction should therefore be materially aligned. Differences in native-price performance can be explained primarily by the trading-currency path, while small residual gaps may still arise from market hours, stale/missing bars, provider adjustments, liquidity and rounding.

The package deliberately uses **historical daily FX rates**. Applying today's FX rate to every historical point would only rescale a series and would not remove the historical currency effect.

## Files

- `sql/20261023_entity_performance_fx.sql`: table, indexes, timestamp triggers and stored procedures.
- `tests/test_entity_performance_fx.sql`: transactional smoke tests.

## Apply

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f sql/20261023_entity_performance_fx.sql
```

## Test

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f tests/test_entity_performance_fx.sql
```

## API-facing stored procedure

```sql
SELECT *
FROM public.sp_get_entity_performance(
    :entity_id,
    :user_id,
    :date_from,
    :date_to,
    :reporting_currency
);
```

The result includes native and reporting-currency closes plus rebased percentage performance for each listing, with ISIN exposed for frontend grouping.

## Important ingestion requirement

The configured EXCHANGE_RATES provider should populate one row per currency and effective date through `sp_upsert_exchange_rate_history`. No provider-specific SQL is required in application code.
