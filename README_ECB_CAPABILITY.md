# ECB exchange-rate capability

This package moves ECB into `market_data.providers` and adds the
`exchange_rates` capability to the shared provider registry.

## Install

Extract this archive directly in `/opt/mip/mip-backend` so the contained paths
replace the active files.

## Configuration

Optional settings:

```dotenv
MARKET_DATA_EXCHANGE_RATES_ORDER=ecb
EXCHANGE_RATES_HTTP_TIMEOUT=30
EXCHANGE_RATES_HTTP_MAX_RETRIES=3
EXCHANGE_RATES_HTTP_RETRY_BASE_SECONDS=1
```

## Validate

```bash
python -m py_compile \
  market_data/capabilities.py \
  market_data/config.py \
  market_data/models.py \
  market_data/providers/ecb.py \
  market_data/service.py \
  market_data/factory.py \
  services/exchange_rates.py \
  jobs/exchange_rates/refresh_exchange_rates.py \
  scripts/refresh_exchange_rates.py

pytest -q tests/test_ecb_market_data_provider.py
./scripts/check.sh
```

## Smoke test

```bash
python -m scripts.refresh_exchange_rates
```

After all tests pass, the old top-level `providers/` directory can be removed,
provided no remaining imports reference it.
