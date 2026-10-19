MIP shared LLM evidence component, phase 3

Prerequisites
-------------
- Shared root llm component installed.
- Market-data phase 1 installed.
- Market-analysis phase 2 installed.

Purpose
-------
Move LLM source/evidence selection out of Portfolio Intelligence and into the
shared llm component. The logic is provider-independent and operates on the
source dictionaries already returned by LLMResponse.

New shared file
---------------
llm/evidence.py

Replaced files
--------------
llm/__init__.py
jobs/portfolio_intelligence/branches/daily_mover.py

Obsolete directory to delete
----------------------------
jobs/portfolio_intelligence/evidence/

Behavior
--------
select_sources():
- accepts requested source indices and available LLM sources;
- preserves the order requested by the validating model;
- removes duplicate indices;
- ignores invalid indices and malformed sources;
- returns dictionary copies to avoid mutating LLMResponse.sources.

A temporary validated_sources alias exists in llm/evidence.py for incremental
migration, but Portfolio Intelligence uses the new select_sources name.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-llm-evidence-phase3.zip -d /opt/mip/mip-backend
rm -rf jobs/portfolio_intelligence/evidence

Validation
----------
python -m compileall llm jobs/portfolio_intelligence
python -m unittest tests.test_llm_evidence

grep -RIn --exclude-dir=.git --exclude-dir=__pycache__   "portfolio_intelligence.evidence" .

The grep output must be empty.

Restart
-------
systemctl restart mip-scheduler
journalctl -u mip-scheduler -n 100 --no-pager

Database
--------
No SQL migration is required for phase 3.
