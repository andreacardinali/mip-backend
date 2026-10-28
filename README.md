# Market Analysis LLM request logging

Adds explicit logs immediately before and after each provider call, including call number, mode, signal count, signal IDs, output-token limit and elapsed time. Batch failures log split sizes and error details before recursive retry.

## Centralized market data

Screener price and candle acquisition uses `market_data` and its configured
providers. The analysis scan requests history through `get_ohlcv`; legacy
Yahoo / Yahoo HTML / Finviz price and candle acquisition is not restored.

`market_sources` remains separate for universe membership (Wikipedia) and
discovery: `REDDIT_TRENDING` through ApeWisdom, and `GAINERS`, `LOSERS` and
`MOST_ACTIVE` through Yahoo JSON with Yahoo HTML, Finviz and StockAnalysis
fallbacks. These providers return candidate symbols and ranking metadata,
not price history or indicator inputs. Order is configurable through
`MARKET_SOURCES_DISCOVERY_ORDER`; individual movers providers accept
`<PROVIDER_NAME>_USER_AGENT` and `<PROVIDER_NAME>_HTTP_TIMEOUT` settings.

Offline coverage lives in `tests/test_market_analysis_scan.py`,
`tests/test_market_data_routing.py`, `tests/test_market_sources_discovery_routing.py`
and `tests/test_wikipedia_provider.py`. No database migration or job activation
is needed for this separation.

### Multi-category discovery refresh

The discovery refresh job now requests Reddit trending, gainers, losers and most
active candidates independently. Configure each category through
`MARKET_DISCOVERY_<CATEGORY>_ENABLED` (default `true`) and
`MARKET_DISCOVERY_<CATEGORY>_LIMIT` (default `100`, range `1` to `100`), where
`<CATEGORY>` is `REDDIT_TRENDING`, `GAINERS`, `LOSERS` or `MOST_ACTIVE`.
`MARKET_DISCOVERY_RETENTION_DAYS` defaults to `90`. These values use the centralized
settings loader. Successful snapshots commit independently; category failures
produce a `PARTIAL` result when other categories succeed.

The resolver and scan default to the latest snapshot per enabled discovery source
category, across providers. An explicit `snapshot_id` still selects only that
snapshot. If a refresh fails, its category can retain an older snapshot until
retention removes it. Disabling a refresh category stops acquisition, but does not
disable previously persisted source records. Scan candidates are deduplicated by
instrument before applying CORE/DISCOVERY limits. Movers use normalized rank on a
0-100 attention scale; Reddit retains its social scoring. Provider metadata is
preserved for traceability, not used to acquire prices or calculate indicators.

Unlike provider routing alone, this resolver/scan update requires replacing
`sp_market_discovery_build_candidates` and
`sp_market_discovery_list_scan_candidates` in an existing PostgreSQL database.
Both definitions are included in the upgrade runner below. No migration or job
activation is performed automatically.

## Intelligence fixes: database upgrade

Editing `mip_schema.sql` does not upgrade an existing database. Back up the database
and stop the backend and all scheduler processes before upgrading. In particular,
old scheduler versions do not participate in the new advisory-lock protocol.

Run from the backend root using the project's Python environment:

```sh
python -m scripts.apply_intelligence_fixes
python -m scripts.apply_intelligence_fixes --apply
```

The first command prints SQL without changing the database. Review that output
before the second command, which uses the configured `DATABASE_URL`. It adds
nullable historical-cost columns and replaces only the reviewed functions
from this checkout. The upgrade is transactional and safe to repeat; no legacy
historical acquisition costs are guessed or backfilled. Keep this checkout with
the deployment record because the migration reads its schema definitions.

The runner executes DDL with `no_parameters=True`, preserving literal `%` markers
inside PL/pgSQL without psycopg interpreting them as driver placeholders. A failed
upgrade rolls back its transaction; rerun the corrected runner after a failure.

Apply the migration before starting the updated backend. Restart the scheduler
with the price-refresh dispatcher enabled: `/api/market-data/prices/refresh` now
returns HTTP 202 after enqueueing work, rather than processing the global queue
inside the request. The frontend must be upgraded with the backend. Job locks use
a dedicated database connection throughout each execution; size the connection
pool accordingly and ensure server idle-in-transaction timeouts allow job runs.

## Financial semantics

`cost_basis_amount` is the total historical acquisition cost of a position in
`cost_basis_currency`, not a per-unit price. It can be supplied or cleared through
the position editor/API. Changing quantity, average price or instrument invalidates
the stored total unless a replacement cost is explicitly supplied.

Portfolio invested value and P&L require known historical costs in the requested
currency. Same-currency quantity times average price remains valid. Otherwise,
unknown acquisition FX yields `null`/`N/D`; current FX is used for market value,
never to invent acquisition cost. A stored cost in one currency does not reconstruct
its historical value in another. These are price-based metrics, not a transaction
ledger with fees, dividends, tax lots or realized gains. Position-row P&L remains
the original listing-currency quantity/average-price metric.

Intelligence allocations use market values converted to the user's reporting
currency. Candidate `currency_code` still identifies the listing currency, not
the aggregate market-value currency. Watchlist entries are entity-based and select
one deterministic listing by instrument ID; a preferred listing is not persisted.
Their target price therefore refers to that representative listing.

## Focused verification

```sh
pytest -q tests/test_alert_evaluator.py tests/test_auth_security.py tests/test_entities_import_options.py tests/test_intelligence_migration.py
pytest -q tests/test_intelligence_api_fixes.py
pytest -q tests/test_sql_repository_contract.py tests/test_intelligence_migration.py
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f tests/test_intelligence_fixes.sql
bash scripts/check.sh
```

For `psql`, use a PostgreSQL connection URI, without the SQLAlchemy driver suffix.
In the frontend root, run `npm ci`, `npm run build`, and `npm run lint`, then verify
position editing and asynchronous price updates against the upgraded database.
Also verify manual/scheduled job contention against PostgreSQL before deployment.

## Database access convention

Application code calls stored functions through parameterized `SELECT` statements.
Business queries, joins, aggregates and writes belong in `mip_schema.sql`, not in
Python routers or repositories. Simple standalone `SELECT` queries and test SQL
are allowed exceptions. Migration DDL lives in SQL files; the upgrade runner reads
`scripts/intelligence_fixes_preflight.sql` before applying the schema functions.

Portfolio pricing metadata remains batched through `sp_get_portfolio_pricing_states`.
Historical-cost writes, user preferences, alert candidate selection, scheduler locks,
request deferral and orphan recovery now also use stored functions. Reapply the
upgrade before running this code, even if the earlier intelligence upgrade was
already applied: these additional functions must exist in the database.
