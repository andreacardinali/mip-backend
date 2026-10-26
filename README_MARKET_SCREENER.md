# Market Screener ranking

This patch adds the provider-agnostic final ranking layer over persisted MIP trading signals.

## Install

```bash
unzip -o mip_market_screener_ranking.zip
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/20261026_market_screener_ranking.sql
python -m pytest -q tests/test_market_screener_ranking.py tests/test_market_screener_sql_contract.py
python -m jobs.market_screener_ranking.worker
```

The job is registered disabled by default. Enable it from System Settings after the first manual validation.
