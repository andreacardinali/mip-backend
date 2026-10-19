MIP optimization bundle, phases 6 through 10 where safe in one pass
Base commit: a03fd59b7c8b3dbe1fefa5f68d5f607262382018

Implemented
-----------
1. Single transaction owner through job_runtime.run_transactional().
2. Portfolio Intelligence branches no longer commit or rollback.
3. Infrastructure errors propagate to the runtime and scheduler.
4. Local malformed event/entity errors become PARTIAL via BranchResult.add_error().
5. Shared internal batching and deduplication utilities.
6. All Portfolio Intelligence severity policies live under scoring/.
7. Notification message formatting lives under messages/.
8. JobStatus is a shared StrEnum and BranchResult serializes it explicitly.
9. refresh_entities and refresh_exchange_rates use the common transactional runner.
10. Basic Black/Ruff configuration and regression tests are included.

Not implemented intentionally
-----------------------------
A complete typed model for every LLM payload was not forced into this pass. The
provider schemas are still evolving, and premature strict models could reject
valid provider responses. The new boundaries make that a safe future change.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-optimization-bundle.zip -d /opt/mip/mip-backend

Validation
----------
python -m compileall job_runtime jobs
python -m unittest tests.test_portfolio_intelligence_refactor

grep -RIn --exclude-dir=.git --exclude-dir=__pycache__   -E "context\.db\.(commit|rollback)"   jobs/portfolio_intelligence

The grep output must be empty.

Optional quality checks, if installed
-------------------------------------
black --check .
ruff check .

Restart
-------
systemctl restart mip-scheduler
journalctl -u mip-scheduler -n 100 --no-pager

Database
--------
No SQL migration is required.
