MIP shared market-data component, phase 1
Base repository commit inspected: ae55cbbab2853b93103eba45481da6b516c12daf
Target state assumes the shared root llm component has already been installed.

Purpose
-------
Extract Yahoo history retrieval from jobs/portfolio_intelligence into a reusable
root market_data package. Portfolio Intelligence keeps only the functional
history period and interval. Provider selection and HTTP timeout belong to the
shared market-data component.

New files
---------
market_data/__init__.py
market_data/base.py
market_data/config.py
market_data/factory.py
market_data/models.py
market_data/service.py
market_data/providers/__init__.py
market_data/providers/yahoo.py

Replaced files
--------------
jobs/portfolio_intelligence/config.py
jobs/portfolio_intelligence/context.py
jobs/portfolio_intelligence/runtime.py
jobs/portfolio_intelligence/branches/daily_mover.py

Obsolete file to delete
-----------------------
jobs/portfolio_intelligence/market/yahoo.py

The indicators module remains in Portfolio Intelligence during phase 1:
jobs/portfolio_intelligence/market/indicators.py

Environment
-----------
Add MARKET_DATA_PROVIDER=yahoo if you want provider selection explicit.
MARKET_DATA_HTTP_TIMEOUT remains the shared HTTP timeout.
YAHOO_HISTORY_RANGE and YAHOO_HISTORY_INTERVAL remain functional settings of
Portfolio Intelligence in this phase, so existing .env values remain valid.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-market-data-phase1.zip -d /opt/mip/mip-backend
rm -f jobs/portfolio_intelligence/market/yahoo.py
python -m compileall market_data jobs/portfolio_intelligence

Check that no obsolete imports remain:
grep -RIn --exclude-dir=.git --exclude-dir=__pycache__   "portfolio_intelligence.market.yahoo" .

Smoke test configuration and factory:
python - <<'PYTEST'
from config.settings import settings
from market_data.config import MarketDataConfig
from market_data.factory import create_market_data_service

config = MarketDataConfig.from_settings(settings)
service = create_market_data_service(config)
print(type(service.provider).__name__, config)
PYTEST

Then restart the scheduler:
systemctl restart mip-scheduler
journalctl -u mip-scheduler -n 100 --no-pager

Database
--------
No SQL changes are required. This phase changes only Python component boundaries
and dependency injection.
