# Patch freschezza prezzi e ricerca Yahoo

```bash
cd /opt/mip/mip-backend
unzip -o /percorso/mip-market-price-freshness-search-fix.zip -d .
source .venv/bin/activate
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/20261023_market_price_freshness.sql
python apply_patch.py
python -m compileall -q market_data services routers
pytest -q
```

Configurazione consigliata:

```env
MARKET_DATA_MAX_QUOTE_AGE_HOURS=96
MARKET_DATA_SYMBOL_SEARCH_ORDER=yahoo,eulerpool,ghostfolio,fmp
```

Riavvia backend e processo scheduler/worker prezzi.
