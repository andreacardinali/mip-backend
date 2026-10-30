# Market Analysis LLM request logging

Adds explicit logs immediately before and after each provider call, including call number, mode, signal count, signal IDs, output-token limit and elapsed time. Batch failures log split sizes and error details before recursive retry.

## Temporary Development Utilities

MIP currently has a single live development instance. Periodic database dumps
record the current schema; migration utilities are not permanent runtime tools.
Keep all one-off migrations, installation SQL, fixes and their preflight/audit
files in `oneshot/`. Keep recurring operational commands in `scripts/`.
Run Python utilities from the backend root with `python -m oneshot.<module>`
using the backend virtual environment. Nothing in `oneshot/` runs automatically.
The directory can be cleaned after development; also remove or update tests and
documentation that reference the deleted migration files.

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

### FMP Quota Cooldown

FMP HTTP 429 responses whose JSON `Error Message` contains `Limit Reach` stop
retries immediately. Market-data providers and FMP jobs share a cooldown per API
key within the current process. During cooldown, calls fail locally with
`ProviderRateLimitError`, without HTTP requests. Ordinary 429 responses retain
their retry policy. A caller-supplied `response_retry_filter` is preserved;
the shared HTTP client evaluates this filter on responses except HTTP 403.

Set `FMP_QUOTA_COOLDOWN_SECONDS=86400` in `.env` to configure the suspension
(default: 24 hours; finite nonnegative seconds, with zero disabling suspension).
After expiry, requests resume automatically. Cooldown is in memory: restarting
the process clears it, and separate worker processes do not share its state.
Requests already in flight are not cancelled. The triggering HTTP error retains
status 429; locally blocked calls have no HTTP response.

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
`oneshot/market_time_audit.sql` with PostgreSQL tooling, review types, function
ownership/grants and dependencies, and take a backup. The audit does not establish
the original timezone of historical values. Do not relabel naive historical
timestamps as UTC without verified provenance and a separately reviewed migration.

From the backend root on Windows:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_market_time
```

This prints the transaction only and does not connect to the database. Review the
SQL, stop backend/scheduler writers, and only then explicitly apply:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_market_time --apply
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
Holiday acquisition verifies the requested 4-20 character `exchange_code` against
that catalog and calls `/market/holidays/{exchange_code}`. Valid code syntax
and provider membership do not independently certify an ISO MIC identity.

The response label (for example `NYSE`) is provider evidence, not a new exchange
identity. Provider aliases must be reviewed and registered explicitly through
`sp_register_exchange_alias`; the full synchronization CLI does not accept a
single-exchange override. Matching a catalog code alone does not prove that a
legacy MIP row represents the same venue.

`exchange_holiday_snapshots` retains original JSON and provider versions.
`as_of` and holiday dates remain dates; `updated_at` and `fetched_at` are aware UTC
instants stored as `timestamptz`. Adjusted opening/closing hours remain local times,
not UTC instants. Conflicting closure flags, response identities and duplicate
holiday dates are rejected. Null closure flags remain unknown.

### Provider Exchange Aliases

`exchanges.exchange` is the internal venue identity; provider codes are aliases
in `exchange_source_mappings`. Apply `oneshot/install_exchange_aliases.sql` to
install conflict-checked alias registration and update listing normalization.
This migration does not register aliases, rename exchanges or move instruments.

After reviewing venue identity, register each provider identifier against the
chosen existing MIP row. For example, if that row is `XNAS` and both external
systems are active:

```sql
BEGIN;
SELECT public.sp_register_exchange_alias('XNAS', 'fmp', 'NASDAQ');
SELECT public.sp_register_exchange_alias('XNAS', 'eulerpool', 'XNAS');
SELECT public.sp_register_exchange_alias('XNAS', 'eulerpool', 'NASDAQ');
COMMIT;
```

Registration is case-insensitive, whitespace-trimmed and idempotent. Conflicting
code or name mappings are rejected, not reassigned. Listing imports resolve
provider aliases before checking existing exchange codes and before exchange
creation; ambiguous mappings fail instead of selecting the latest observation.
Before catalog authority is activated, unknown identifiers retain the existing
fallback behavior and can produce distinct venues. After activation, exchange
upserts require canonical catalog membership. Review aliases before new-provider
imports. Provider identity must be included in the profile's source metadata.
These mappings resolve incoming identifiers, not outbound API parameter choices.

Existing `NASDAQ`/`XNAS` rows and their instrument references are not merged by
registering aliases. The unified job refuses to synchronize a catalog while
noncanonical rows remain; neither provider acceptance nor valid code syntax
code alone proves that two venues are equivalent.

The existing `exchange_source_mappings` records the requested code and observed
provider label with endpoint/version evidence. Ambiguous aliases or canonical-code
links fail rather than overwrite existing mappings. Identical provider versions
are idempotent; changed JSON for the same version is rejected. Migration and initial
acquisition are explicit commands, not API side effects:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_exchange_registry
.\.venv\Scripts\python.exe -m scripts.sync_exchanges
```

Both commands above are dry-runs: no database or HTTP access. Before activation,
review the schema SQL, ownership/grants, existing exchange mappings and backup.
The schema migration requires existing `scheduler_jobs`, `exchanges`, `external_systems` and
`exchange_source_mappings` tables. It is a one-time migration and deliberately
fails if its objects already exist. After review, explicit application is:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_exchange_registry --apply
.\.venv\Scripts\python.exe -m scripts.sync_exchanges --apply
```

Acquisition requires `EULERPOOL_API_KEY` and exactly one active external-system
row with code `eulerpool` (case-insensitive). All database writes use stored
functions in transactions. No external calls or migrations were executed during
implementation. Validate the SQL on a test PostgreSQL database before production.

The `eulerpool_exchange_sync` scheduler job first synchronizes the complete
Eulerpool catalog into `public.exchanges`, then acquires or refreshes holidays for
all registered exchanges. Catalog codes (4-20 uppercase alphanumeric characters)
are authoritative; names, country and supplied IANA timezones are updated, and the
full provider payload is retained in `exchange_source_mappings`. Missing timezones
preserve existing values. Calendar names and regular trading hours are not inferred.
The orchestration lives in `market_data.exchange_sync`, with a thin job entrypoint
in `jobs.eulerpool_exchange_sync`. The CLI uses the same complete flow.
Bootstrap at scheduler startup discovers its manifest and registers new jobs
disabled by default. Enable it
through the existing scheduler controls; manual initial acquisition is optional.
Its daily cron is `30 6 * * *`, interpreted in `SCHEDULER_TIMEZONE` (default
`Europe/Rome`), not necessarily UTC. Fresh exchange-registry migrations include the
catalog functions. For an already installed registry, review this upgrade first:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_exchange_registry --upgrade
```

Only after SQL review, backup and validation on a test PostgreSQL database, repeat
with `--upgrade --apply`. The upgrade widens holiday exchange codes and replaces
the holiday storage, canonical exchange upsert and catalog synchronization functions.
It does not reconcile existing exchange rows.

### Alert Rule Management

Account settings, watchlist details, portfolio details and Screener share monitoring
rule controls through authenticated `GET /api/alerts`, `POST /api/alerts` and
`PUT /api/alerts/{rule_id}`. Collection views show their dedicated rules;
account settings retain access to all scopes.
Rules are scoped to the authenticated user, including disabled rules. Creation
reuses `sp_alert_upsert_rule`: submitting an existing name updates that user's rule.
Updates by UUID never modify another user's rule. Quick activation uses
`PATCH /api/alerts/{rule_id}/enabled` with a strict boolean `enabled` and changes
no conditions. `DELETE /api/alerts/{rule_id}` removes the rule and its event history
via the existing cascade constraint; the UI requires explicit confirmation.
Both operations return 204 on success or 404 for missing/foreign rules.
Disable rather than delete to preserve history. Disabling prevents new matches,
but does not cancel notifications already queued. Delivery uses the existing pipeline.

Before using this API on the live instance, review and manually apply
`oneshot/install_alert_rule_management.sql` after backup and PostgreSQL validation.
Reapply it even if an older version was installed: it now installs
`sp_alert_list_user_rules`, `sp_alert_set_rule_enabled` and `sp_alert_delete_rule`.
No startup migration is performed. `mip_schema.sql` contains the same routines.
No live migration was run. SQL contract tests compare definitions and ownership
guards, but do not substitute for PostgreSQL syntax and runtime validation.

Conditions accept symbols, directions, verdicts, origins and supporting strategy
codes, plus normalized score thresholds (0 to 1), minimum strategy count and
cooldown in minutes. Empty filter lists impose no restriction. Multiple selected
strategy codes match any supporting strategy; the evaluator remains centralized.

### Collection Monitoring Runtime

The existing market alert evaluation job now loads candidates per enabled rule
through `sp_alert_list_rule_candidates`, rather than sharing a global Screener
candidate list. Watchlist and portfolio membership and ownership are selected in
the stored routine; each rule is evaluated only against its own returned rows.
The `candidates` metric counts rows across rules, including repeated instruments.
`max_candidates` limits each rule separately.

Before running this updated job, review and manually apply
`oneshot/install_collection_monitoring.sql` after backup and PostgreSQL validation.
It installs the candidate routine, permits the four alert types and makes event
`signal_id` nullable for price monitoring. `mip_schema.sql` contains the matching
definition. No live SQL was executed and no startup migration is installed.

Supported types are `MARKET_SIGNAL`, `FIXED_PRICE`, `PERCENT_MOVE` and
`SIGNAL_CHANGE`. Scope accepts `SCREENER`, `WATCHLIST`, `PORTFOLIO` or `ALL`;
`scope_id` optionally narrows watchlist or portfolio rules to one owned collection.
Price rules use fresh instrument prices. Percent movement uses the last positive
stored price within the previous exchange session's UTC opening/closing bounds,
within seven days. This is not guaranteed to be the official closing auction
price. Missing exchange calendars or in-session reference data skip evaluation.
Daily bars timestamped outside those bounds are not used as a fallback.
Lifecycle monitoring excludes `OBSERVED` events and uses event creation time.

Review and manually apply `oneshot/install_screener_monitoring_extensions.sql`
after the collection monitoring and history installers, with backup and PostgreSQL
validation, before activating this worker. The matching definitions are in
`mip_schema.sql`; no startup migration or live SQL execution is performed.
This installer also extends shared scan ingestion to watchlist instruments and
nonzero portfolio positions outside the selected universe. Alert selection remains
rule/owner scoped in `sp_alert_list_rule_candidates`.

Rules support UUID-keyed `instrument_overrides`, including instrument pause,
threshold, direction and strategy/lifecycle filters. `crossing_only` defaults to
true for newly saved rules: the first valid matching observation may alert, then
an observed non-match rearms persistent state. Continued matches reuse a generation;
older or repeated observations, stale/invalid prices and excluded instruments
cannot rearm it. Changed thresholds, percent references or movement direction
create a distinct state. Existing cooldown and event deduplication still apply.

`regular_session_only` requires both observation and evaluation to belong to the
same open session. `once_per_session` defaults to true for newly saved rules and
suppresses events by instrument, observed session and actual direction; missing
calendar context skips evaluation. `notification_mode` accepts `IMMEDIATE` or
`DIGEST`; digest combines events from one worker run by owner, channel, type and
severity through the existing queue, not a scheduled daily summary. Legacy rules
without these options retain their existing crossing/session defaults.

Delivery retains the central queue and passes the actual rule type, including
for events without a signal ID. Account settings provide type, scope, optional
owned collection, excluded symbols, price thresholds and maximum data age controls.
Collection choices reuse the existing watchlist and portfolio APIs.

To expose all four monitoring types in the existing notification preferences API,
review and manually apply
`oneshot/install_monitoring_notification_preferences.sql` after backup and
PostgreSQL validation. Apply it after the alert management and collection monitoring
installers above, before using the updated Account controls. It replaces only
`sp_get_user_notification_preferences` and `sp_upsert_user_notification_preference`;
the same definitions are present in `mip_schema.sql`. No live SQL was executed.
Monitoring preferences default to enabled, preserving existing fallback delivery.
Users can disable each type or select an enabled notification destination; existing
ownership and minimum-severity validation remain unchanged. Without a matching
subscription, the queue still uses its configured channel/source fallback.

### Alert Event History

Account rule settings expose paginated event history through authenticated
`GET /api/alerts/{rule_id}/events?limit=25&offset=0`. The endpoint calls only
`sp_alert_list_user_events`, which filters rule, event and notification ownership.
Missing or foreign rules return an empty list. Limits are 1 to 100 and offsets
are 0 to 100000. Events are ordered by creation time and event UUID descending;
offset pagination can shift when new events arrive. Refresh returns to page one.

Before using history, review and manually apply
`oneshot/install_alert_event_history.sql` after backup and PostgreSQL validation,
after the alert management and collection monitoring installers. The matching
function is included in `mip_schema.sql`; no live SQL or startup migration ran.

History shows the saved event context and current queue status, not an audit
trail of every delivery attempt. Events without a notification are labelled
not queued. Newly registered events persist the shared queue's skip reason:
notifications disabled, preference disabled, destination unavailable or duplicate.
The API exposes `notification_skip_reason`; older events retain an unknown reason.
Reapply the updated history installer before deploying this worker version,
even if history was already installed. No live SQL was executed.
Matches suppressed by
deduplication or cooldown create no event and do not appear. Notification errors
and destination details are not exposed. Price events can have no signal ID.

Offline checks for alert configuration, evaluation, API ownership/transactions and
stored-routine contracts:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_alert_rules.py tests/test_alert_evaluator.py tests/test_sql_repository_contract.py -q -rs
```

API tests use simulated authentication and database sessions, never the live
database. They require FastAPI and HTTPX from the declared dependencies; skipped
API tests do not validate the endpoints. Real authentication, PostgreSQL ownership
and notification delivery still require integration verification before activation.

### Scheduler Job Rename

Stop backend/scheduler writers before applying the migration, and complete it
before starting the new code or manifest bootstrap. The registry migration also
calls `sp_migrate_exchange_sync_job`, renaming the persisted
`eulerpool_exchange_holidays` row to `eulerpool_exchange_sync`. It changes only
the job key, display name, Python module and update timestamp. The UUID,
enablement, trigger, schedule, run timestamps, history and queued requests remain
unchanged. If both keys already exist, it aborts instead of merging or deleting
either job. Resolve that conflict explicitly before proceeding.

For databases whose exchange registry is already up to date, review the standalone
rename SQL, then apply it only after backup and PostgreSQL validation:

```powershell
.\.venv\Scripts\python.exe -m oneshot.apply_exchange_registry --rename-job-only
.\.venv\Scripts\python.exe -m oneshot.apply_exchange_registry --rename-job-only --apply
```

This mode is repeatable and does nothing if the legacy job is absent. After the
migration, restart the scheduler. Old Python entrypoint and CLI names are removed;
update operational commands to use the new names. No migration runs automatically
on application startup.

Each run retrieves one live catalog through the centralized HTTP client. Malformed
or empty catalogs fail the run before holiday acquisition. The catalog transaction
is committed before holidays begin, and the same response is reused for all venues.
Existing noncanonical rows (for example `NASDAQ` instead of `XNAS`) or conflicting
Eulerpool mappings block catalog synchronization: reconcile them explicitly first.
No automatic merges, instrument moves or exchange deletions are performed. Once
catalog authority is activated, asset ingestion must resolve provider aliases to
catalog codes rather than create new exchange identities.
Existing snapshot codes take precedence,
followed by Eulerpool source mappings, then the MIP exchange code/full name when
no prior identity is available. Resolution requires an exact, unique catalog
code or case-insensitive trimmed name; no hardcoded aliases or fuzzy matching
are used. Stale canonical codes, unresolved mappings, ambiguous matches and
multiple MIP rows resolving to the same code are skipped with a warning and a
per-exchange failure reason. Each snapshot is stored in a separate transaction;
a failed exchange does not prevent the others from updating. Results include per-exchange
outcomes and `eligible`, `upserted`, `failed` counters. `upserted` counts successful
stores, including idempotent existing snapshots. Results are `SUCCESS`, `PARTIAL`
or `FAILED`; `catalog_upserted` counts synchronized catalog entries. An empty MIP
exchange table is populated from the catalog before holiday acquisition. Existing
rows need no initial snapshot to be eligible. Catalog, configuration and
target-selection failures fail the whole run. A holiday failure does not roll back
the already committed catalog. Successful acquisition stores the verified provider
identity through the existing stored function.

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
.\.venv\Scripts\python.exe -m ruff check market_time routers/market_time.py oneshot/apply_market_time.py tests/test_market_time.py tests/test_fmp_time.py jobs/fmp/normalizers.py jobs/fmp/fmp_daily_sync.py services/news_ingestion.py market_data/providers/eulerpool.py db.py
```

Calendar/DST/API tests can be skipped when local dependencies are unavailable;
skips are not successful validation of those integrations. Before activation,
rerun them with dependencies installed and verify the migration and persisted
values on a test PostgreSQL database. Remaining screener migration phases and
frontend integration are outside this foundation.

## Entity cleanup job

Apply `oneshot/install_cleanup_entities.sql` to an existing PostgreSQL database
before running `cleanup_entities`. New databases include these functions in
`mip_schema.sql`. Scheduler startup discovers `jobs/cleanup_entities.job.json`
and registers the job disabled by default. Run it manually through the existing
scheduler controls before enabling its daily `30 4 * * *` schedule, interpreted
in `SCHEDULER_TIMEZONE`.

To preview candidates after installing the functions:

```sql
SELECT entity_id FROM public.sp_list_orphan_entities();
```

The job deletes entities with no instruments, committing each entity separately.
It locks and rechecks each candidate, skipping busy, missing or newly linked
entities. Existing foreign keys remain authoritative: cascading metadata is
deleted, but references that prohibit deletion (including watchlist/discovery
items) leave the entity intact. Results report `candidates`, `deleted`,
`referenced` and `skipped`; unexpected errors stop the run without undoing earlier
commits. Validate the installation and concurrency behavior on a test PostgreSQL
database before production use.

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

### Screener snapshot changes and opt-in summary

For an existing database, review and manually apply
`oneshot/install_screener_snapshot_changes.sql`, then
`oneshot/install_screener_summary_notifications.sql`. The authoritative
`mip_schema.sql` already contains these routines. There is no automatic startup
installation. Validate SQL installation on a test database before activation.

The Screener variations view compares complete successful snapshots, independent
of the current grid filters, and identifies new entries, signal changes and exits.
Its detail action opens the existing current opportunity and closes the variations
dialog first. Exited rows retain their historical comparison but cannot open a
current opportunity through this action. The detail is live, not a frozen snapshot.

In account notification preferences, `SCREENER_SUMMARY` is disabled by default.
Enable it explicitly with an enabled user-owned destination; the existing minimum
severity preference still applies to this `LOW` event. Only enabled global
subscriptions are recipients. `NOTIFICATIONS_ENABLED` must also be enabled, and
delivery uses the existing notification queue and dispatcher.

The pipeline enqueues after committing a successful run whose analysis scan and
ranking both succeeded. Initial snapshots, unchanged comparisons and incomplete
runs do not enqueue summaries. Messages contain only new, changed and removed
counts, not detailed opportunity rows. The key is unique per user and snapshot;
an enqueue failure is reported without failing the already persisted snapshot.

Offline contracts are in `tests/test_market_screener_signal_runtime_contract.py`
and `tests/test_sql_repository_contract.py`. They do not replace database or
interactive GUI verification.

### Screener signal history

The Screener opportunity detail now includes signal lifecycle history for its
source instrument, across strategies and timeframes. The authenticated
`GET /api/screener/{opportunity_id}/signal-events` route resolves the opportunity
through `MarketScreenerApiRepository` with the current user, then delegates to
`SignalLifecycleRepository` and the existing
`sp_market_analysis_list_signal_events` routine. Unknown opportunities return
404; invalid UUIDs and limits return 422. `limit` defaults to 100 (range 1-1000).
Database ordering is newest first by observation time and creation time.

The UI displays timestamps in the browser's local timezone, strategy, timeframe,
event, status/direction transitions and reason, with loading, empty, error and
refresh states. This is an event audit, not a legacy entry/exit trade ledger;
there is no history deletion action. No new SQL installer is required when the
existing routine from `mip_schema.sql` is installed. No live database changes
were performed.

Offline tests are in `tests/test_market_screener_signal_runtime_contract.py`.
They exercise source-instrument selection, owner forwarding, missing
opportunities, session cleanup and reuse of the stored routine. HTTP tests cover
authentication and parameter validation when FastAPI/Pydantic and HTTPX are
installed; otherwise those cases are skipped. Frontend source-contract checks
do not replace a TypeScript build or interactive UI validation.

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
python -m oneshot.apply_intelligence_fixes
python -m oneshot.apply_intelligence_fixes --apply
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
`oneshot/intelligence_fixes_preflight.sql` before applying the schema functions.

Portfolio pricing metadata remains batched through `sp_get_portfolio_pricing_states`.
Historical-cost writes, user preferences, alert candidate selection, scheduler locks,
request deferral and orphan recovery now also use stored functions. Reapply the
upgrade before running this code, even if the earlier intelligence upgrade was
already applied: these additional functions must exist in the database.
