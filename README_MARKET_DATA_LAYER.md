# Capability-driven market-data layer

Built against mip-backend commit `64d816275bfb2602f9c7f6882b9ba605cc1ddc0d`.

## Install

Extract into `/opt/mip/mip-backend` and merge `.env.market-data.example` into `.env`.

The existing `providers.manager.market_data` remains as a compatibility adapter, while new code should import `market_data.factory.market_data`.

## Checks

```bash
python -m py_compile market_data/*.py market_data/providers/*.py providers/manager.py
pytest -q tests/test_market_data_routing.py tests/test_eulerpool_normalization.py
ruff check market_data providers/manager.py tests/test_market_data_routing.py tests/test_eulerpool_normalization.py
black --check market_data providers/manager.py tests/test_market_data_routing.py tests/test_eulerpool_normalization.py
```

## Capability diagnostics

Register `routers.market_data.router` under `/api/market-data` in `main.py` if you want the optional diagnostics endpoint. The package does not modify `main.py` automatically to avoid conflicting with local router registration changes.

## Migration strategy

The compatibility adapter lets the current search/import/entity-refresh/price-refresh flows continue without immediate router changes. Migrate consumers incrementally to `MarketDataService`, then remove the legacy `providers` package after Ghostfolio and FMP no longer need their adapters.
