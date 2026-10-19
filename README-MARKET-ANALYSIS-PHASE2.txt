MIP shared market-analysis component, phase 2

Prerequisite
------------
Phase 1 market_data refactor must already be installed. The replacement
DailyMoverBranch uses context.market_data and receives list[PriceBar].

Purpose
-------
Extract reusable technical analysis from Portfolio Intelligence into the root
market_analysis package while keeping portfolio-specific severity scoring under
jobs/portfolio_intelligence/scoring.

New shared files
----------------
market_analysis/__init__.py
market_analysis/models.py
market_analysis/technical.py

New Portfolio Intelligence file
-------------------------------
jobs/portfolio_intelligence/scoring/__init__.py
jobs/portfolio_intelligence/scoring/daily_mover.py

Replaced file
-------------
jobs/portfolio_intelligence/branches/daily_mover.py

Obsolete file to delete
-----------------------
jobs/portfolio_intelligence/market/indicators.py

If the jobs/portfolio_intelligence/market directory is empty after phase 1 and
phase 2, delete the directory too.

Behavior preserved
------------------
- daily percentage change
- RSI period 14
- SMA 50 and SMA 200
- Bollinger bandwidth based on 20 closes and two standard deviations
- existing prompt/context keys through TechnicalSnapshot.to_dict()
- existing Portfolio Intelligence severity thresholds

Design boundary
---------------
market_analysis contains provider-neutral calculations only.
daily_mover_severity remains domain policy in Portfolio Intelligence.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-market-analysis-phase2.zip -d /opt/mip/mip-backend
rm -f jobs/portfolio_intelligence/market/indicators.py
rmdir jobs/portfolio_intelligence/market 2>/dev/null || true

Validation
----------
python -m compileall market_analysis jobs/portfolio_intelligence
python -m unittest tests.test_market_analysis

grep -RIn --exclude-dir=.git --exclude-dir=__pycache__   "portfolio_intelligence.market.indicators" .

The grep output must be empty.

Restart
-------
systemctl restart mip-scheduler
journalctl -u mip-scheduler -n 100 --no-pager

Database
--------
No SQL migration is required for phase 2.
