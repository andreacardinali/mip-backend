# Market Analysis LLM request logging

Adds explicit logs immediately before and after each provider call, including call number, mode, signal count, signal IDs, output-token limit and elapsed time. Batch failures log split sizes and error details before recursive retry.

## Centralized HTTP Requests

Provider HTTP calls must use `http_client.request_with_retry`; do not introduce
independent retry or credential-redaction implementations in providers or jobs.
The shared module redacts recognized credentials in its diagnostic messages and
`httpx` request logs, including URL/query parameters, headers, client defaults,
and sensitive fields in JSON/form mappings. Authentication sent to providers is
unchanged. Retry and idempotency rules are unchanged, including no retry on 403.

Failures retain their original `httpx` exception types. Callers requiring different
handling without importing `httpx` can use `http_client.get_http_status(exc)`.
It returns the status of an HTTP error or its immediate explicit cause, or `None`
for other errors. Existing callers can still catch `httpx.HTTPStatusError` and
inspect `exc.response.status_code`; network failures remain
`httpx.RequestError` subclasses. Exception messages are sanitized, but request and
response objects retain their original content for programmatic handling. Do not
log those objects, raw bodies, arbitrary authentication objects or exception
causes directly: this module does not sanitize every possible caller log.

## Shared Market Time: Phase One

`market_time` centralizes UTC normalization and exchange session snapshots.
Instants must be aware and are normalized to UTC; PostgreSQL connections request
UTC. Date-only events stay dates: FMP earnings, dividends and splits write
`catalyst_day` via the payload, with no fabricated `catalyst_date` midnight.
Existing historical instants are not rewritten or cleared.

Authenticated `GET /api/market-time/exchanges/{exchange}` returns `OPEN`, `CLOSED`
or `UNKNOWN`, UTC session boundaries, next transitions and calendar provenance.
Optional `at` must include an offset (URL-encode `+`); a naive value returns 422.
Missing exchange mapping, dependencies or calendar coverage returns `UNKNOWN`.
Regular local opening hours are metadata, not a holiday-aware fallback. News and
catalysts are not suppressed when a market is closed. Cron timezone is separate.

### Review and Activation

No installation, database migration or job activation is performed automatically.
Use the backend virtual environment, independently of other workspace roots.
The required `exchange-calendars` and `tzdata` packages are declared in
`requirements.txt`; installing them is a separate authorized step.

Before changing an existing database, run the read-only audit
`scripts/market_time_audit.sql` with PostgreSQL tooling, review types, function
ownership/grants and dependencies, and take a backup. The audit does not establish
the original timezone of historical values. Do not relabel naive historical
timestamps as UTC without verified provenance and a separately reviewed migration.

From the backend root on Windows:

```powershell
.\.venv\Scripts\python.exe -m scripts.apply_market_time
```

This prints the transaction only and does not connect to the database. Review the
SQL, stop backend/scheduler writers, and only then explicitly apply:

```powershell
.\.venv\Scripts\python.exe -m scripts.apply_market_time --apply
```

The transactional upgrade adds nullable `exchanges.calendar_name` and replaces the
legacy naive filing function signature with a `timestamptz` signature. It refuses
unexpected existing temporal column types, unreviewed ownership/grants, and
dependent objects (`RESTRICT`, never `CASCADE`). Resolve such blockers explicitly;
the runner does not convert columns or backfill history. Keep this checkout with
the migration record, since function SQL is read from `mip_schema.sql`.

### Dynamic Exchange Acquisition

The earlier eight-row `market_time_exchange_mappings.sql` proposal and its runner
are superseded and must not be applied as part of this workflow.

Eulerpool exchange resolution uses its live catalog, exact exchange codes first,
then unique exact names. There is no name-to-calendar dictionary in this path.
Holiday acquisition verifies the requested four-character `exchange_code` against
that catalog and calls `/market/holidays/{exchange_code}`. Four-character syntax
and provider membership do not independently certify an ISO MIC identity.

The response label (for example `NYSE`) is provider evidence, not a new exchange
identity. An optional `--exchange` explicitly links the canonical request code to
an existing MIP row without renaming it, creating another row, or changing assets.
This link must be reviewed by the operator; matching a catalog code alone does not
prove that a legacy MIP row represents the same venue.

`exchange_holiday_snapshots` retains original JSON and provider versions.
`as_of` and holiday dates remain dates; `updated_at` and `fetched_at` are aware UTC
instants stored as `timestamptz`. Adjusted opening/closing hours remain local times,
not UTC instants. Conflicting closure flags, response identities and duplicate
holiday dates are rejected. Null closure flags remain unknown.

The existing `exchange_source_mappings` records the requested code and observed
provider label with endpoint/version evidence. Ambiguous aliases or canonical-code
links fail rather than overwrite existing mappings. Identical provider versions
are idempotent; changed JSON for the same version is rejected. Migration and initial
acquisition are explicit commands, not API side effects:

```powershell
.\.venv\Scripts\python.exe -m scripts.apply_market_time_holidays
.\.venv\Scripts\python.exe -m scripts.sync_exchange_holidays XNYS --exchange NYSE
```

Both commands above are dry-runs: no database or HTTP access. Before activation,
review the schema SQL, ownership/grants, existing exchange mappings and backup.
The schema migration requires existing `exchanges`, `external_systems` and
`exchange_source_mappings` tables. It is a one-time migration and deliberately
fails if its objects already exist. After review, explicit application is:

```powershell
.\.venv\Scripts\python.exe -m scripts.apply_market_time_holidays --apply
.\.venv\Scripts\python.exe -m scripts.sync_exchange_holidays XNYS --exchange NYSE --apply
```

Acquisition requires `EULERPOOL_API_KEY` and exactly one active external-system
row with code `eulerpool` (case-insensitive). All database writes use the stored
function in a transaction. No external calls or migrations were executed during
implementation. Validate the SQL on a test PostgreSQL database before production.

The `eulerpool_exchange_holidays` scheduler job acquires missing holiday snapshots
and refreshes existing ones for rows in `public.exchanges`. Bootstrap at scheduler
startup discovers its manifest and registers it disabled by default. Enable it
through the existing scheduler controls; manual initial acquisition is optional.
Its daily cron is `30 6 * * *`, interpreted in `SCHEDULER_TIMEZONE` (default
`Europe/Rome`), not necessarily UTC. No additional schema migration is required.

Each run retrieves one live catalog. Existing snapshot codes take precedence,
followed by Eulerpool source mappings, then the MIP exchange code/full name when
no prior identity is available. Resolution requires an exact, unique catalog
code or case-insensitive trimmed name; no hardcoded aliases or fuzzy matching
are used. Stale canonical codes, unresolved mappings, ambiguous matches and
multiple MIP rows resolving to the same code are skipped with a warning and a
per-exchange failure reason. Each snapshot is stored in a separate transaction;
a failed exchange does not prevent the others from updating. Results include per-exchange
outcomes and `eligible`, `upserted`, `failed` counters. `upserted` counts successful
stores, including idempotent existing snapshots. Results are `SUCCESS`, `PARTIAL`
or `FAILED`; an empty MIP exchange table returns `INCOMPLETE` with reason
`no_registered_exchanges` and a warning, without HTTP requests.
The scheduler repository persists `INCOMPLETE` as `PARTIAL`, matching the database
completion contract, while preserving the original status and reason in the JSON
result. Existing MIP exchange rows need no initial snapshot to be eligible;
unresolved identities still require a verified mapping before acquisition.
Catalog, configuration and target-selection failures fail the whole run. The job
does not create exchange rows or change calendar metadata. Successful acquisition
stores the verified provider identity through the existing stored function.

Holiday snapshots are acquisition evidence only: `as_of` is not calendar coverage.
They do not yet replace the session provider or assign `calendar_name`, IANA
timezones or regular trading hours. Verified coverage and venue-specific calendar
metadata are still required before deriving OPEN/CLOSED; unresolved venues keep
returning UNKNOWN. News/catalyst behavior and historical timestamps are unchanged.

FMP `acceptedDate` with an offset is converted to UTC. Naive filing timestamps are
rejected unless `FMP_ACCEPTED_DATE_TIMEZONE` is set in the backend `.env` to a
verified source timezone. This setting describes FMP acceptance timestamps, not an
exchange timezone; there is no default. Ambiguous/nonexistent local times are
rejected. Logs report temporal rejections; job `normalization_discarded` also counts
other filtering/deduplication, so it is not a temporal-only rejection metric.
News persistence rejects naive instants; Eulerpool ISO timestamps without offsets
are discarded rather than treated as UTC. Provider timezone assumptions require
separate verification.

### Focused Checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_time.py tests/test_fmp_time.py tests/test_news_ingestion.py -q -rs
.\.venv\Scripts\python.exe -m ruff check market_time routers/market_time.py scripts/apply_market_time.py tests/test_market_time.py tests/test_fmp_time.py jobs/fmp/normalizers.py jobs/fmp/fmp_daily_sync.py services/news_ingestion.py market_data/providers/eulerpool.py db.py
```

Calendar/DST/API tests can be skipped when local dependencies are unavailable;
skips are not successful validation of those integrations. Before activation,
rerun them with dependencies installed and verify the migration and persisted
values on a test PostgreSQL database. Remaining screener migration phases and
frontend integration are outside this foundation.

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
