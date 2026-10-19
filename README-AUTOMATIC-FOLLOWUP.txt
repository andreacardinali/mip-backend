MIP automatic follow-up bundle
Base commit: 35dc1baba1e144dea245f2f5d89b2b1ce36aa69f

Automatically implemented
-------------------------
- Daily Mover no longer catches every infrastructure exception.
- Typed parsers for Daily validation, Upcoming events, and Breaking events.
- Rich notification enqueue result with backward-compatible boolean behavior.
- Per-branch notification diagnostics for created, duplicate, disabled, and
  missing-source outcomes.
- Readable Portfolio Intelligence repositories with isolated row mapping.
- requirements-dev.txt for Black and Ruff.
- scripts/check.sh for compile, tests, Black, and Ruff.
- Unit tests for parsers and notification enqueue outcomes.

Install
-------
cd /opt/mip/mip-backend
unzip -o mip-automatic-followup.zip -d /opt/mip/mip-backend
chmod +x scripts/check.sh

Safe validation before formatting
---------------------------------
python -m compileall notifications jobs/portfolio_intelligence
python -m unittest tests.test_notification_enqueue tests.test_event_parsing

The bundle intentionally does not run Black or Ruff fixes. Formatting changes
can be reviewed separately after functional verification.

Database
--------
No SQL migration is required. The existing enqueue stored procedure already
returns NULL when ON CONFLICT DO NOTHING handles a duplicate.
