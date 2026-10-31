# Market Analysis LLM request logging

Adds explicit logs immediately before and after each provider call, including call number, mode, signal count, signal IDs, output-token limit and elapsed time. Content-validation failures log split sizes and error details before recursive retry.

## Scheduler Execution Requests

Explicit execution APIs authenticate, authorize and validate the request, then
enqueue work and return HTTP 202 with `request_id`, `job_run_id` and `QUEUED`.
They do not execute provider calls, analyses or cleanup in the HTTP process.
`GET /api/jobs/runs/{job_run_id}` exposes the authenticated request owner's
execution status, result and error; another user's execution returns 404.

| API | Scheduler job | Request source |
| --- | --- | --- |
| `POST /api/screener/analyze` | `market_screener_context_analysis` | `SCREENER_UI` |
| `POST /api/admin/entities/refresh` | `refresh_entities` | `ENTITIES_UI` |
| `POST /api/admin/cache/cleanup` | `market_cache_cleanup` | `CACHE_UI` |
| `POST /api/market-data/prices/refresh` | `market_price_refresh_request` | `PRICE_REFRESH_UI` |
| `POST /api/admin/system/scheduler/jobs/{job_key}/run` | Requested ordinary job | `ADMIN_UI` |

Each started job logs its caller (`MANUAL`, `SCHEDULE`, or `JOB` for a nested
execution), request source, requesting user and parent run ID when present.
The `requested_by` log field shows the username resolved through the existing
`sp_get_user_context` routine, falling back to the UUID when the user is missing
or the lookup fails. Lookup failures do not stop execution; stored request IDs
and the worker request context retain the original UUID.
These entries are included in the existing persisted run logs. Lock acquisition
and contention handling are unchanged.

Administrators can preview a five-field cron with
`POST /api/admin/system/scheduler/cron-preview` (`cron_expression` in the JSON
body). The endpoint uses the same trigger builder and `SCHEDULER_TIMEZONE` as
the scheduler, returning `next_run_at` and `timezone`; invalid expressions return
422 without opening a database session. Schedule updates use the existing PATCH
endpoint and stored routine. The running scheduler synchronizes changes within
its existing 30-second polling interval. No additional schema installation is
needed for caller logs or cron preview.

Before using these APIs on an existing database, stop the backend and scheduler,
manually apply `oneshot/install_scheduler_execution_requests.sql` after the
Screener installers, then restart
the updated processes. Scheduler bootstrap discovers the job manifests. No
installation SQL runs automatically at application startup.

Screener context analysis and user price-refresh requests cannot be enabled as
recurring jobs or launched from the generic admin run endpoint. Their workers
need contextual request metadata. Independent jobs retain their existing schedules;
the consolidated entity refresh without a request retains its `MISSING` defaults. Pending requests
are deduplicated for the same job, user and validated payload.

Screener retains its advisory locks, incremental commits and domain runs,
correlated through `job_run_id`. A busy analysis is deferred with the same ticket;
lock diagnostics identify the blocking job and run without changing lock policy.
Cache cleanup still uses `sp_cleanup_market_cache(30)`. The price-request worker
only populates the existing specialist queue for the authenticated user with a
30-second freshness threshold. Its successful result means enqueueing completed,
not that all prices are updated: the existing dispatcher performs acquisition,
and the frontend continues price polling after awaiting the scheduler result.
Data-write endpoints may still populate internal queues as part of their writes.

### Consolidated Scheduler Workers

`market_screener_pipeline` is the recurring and generic admin entry point for
discovery, candidate resolution, analysis, AI enrichment and ranking. Its admin
request optionally accepts `phases` and `stop_on_error`. Omitted phases run all
configured phases; selected phases execute once in canonical order and cannot
override server configuration, including disabled AI.

`refresh_entities_missing` is the consolidated entity-refresh entry point;
its parameterized request also supports full refresh. The old full-refresh entry
and standalone screener phase jobs are superseded. Their implementation modules
remain internal dependencies, but their manifests are removed and the scheduler
deletes their database registrations at startup. Generic admin execution,
re-enabling and schedule editing of those aliases return 422. The admin list
shows the current database catalog without a separate superseded-job category.
Advisory locks, coalescing and execution coordination remain unchanged.

At startup, the scheduler validates all discovered `*.job.json` definitions before
opening a database session. It then registers or updates those definitions and
deletes every existing catalog entry whose `job_key` is no longer defined. These
catalog updates commit in one transaction; a failure rolls them all back. An empty
or invalid catalog fails startup without changing existing jobs. Missing or
inaccessible directories, traversal errors, unreadable files and symbolic links
also abort bootstrap before any database access. Partial discovery is never used
to reconcile the catalog.

Manually install `oneshot/install_scheduler_catalog_pruning.sql` before restarting
the updated scheduler. The stored routine removes undefined jobs, their manual
requests, execution history and logs. Screener domain runs are preserved by
clearing their references to deleted scheduler runs. Pruning refuses jobs holding
an execution lock, unfinished runs or claimed incomplete requests; bootstrap
rolls back rather than interrupting execution. Resolve stale unfinished runs
before retrying bootstrap because stale-run recovery runs afterward. No schema
installation runs automatically at startup.

Restart the updated scheduler to reconcile manifest changes. Disabled jobs that
still have a manifest remain in the catalog. The existing enabled preference of a
defined job is preserved; its schedule is synchronized from its manifest at
startup. The 30-second polling loop continues to read database settings without
reloading manifests, preserving runtime schedule edits from the admin interface.

Each manifest must also provide a nonempty `description`. At every startup,
bootstrap imports the description for new jobs and updates it for existing jobs
when the JSON text changes, including disabled jobs. Descriptions are returned
by the admin catalog API for display on the job management page. No execution
or enablement behavior is changed.

Before restarting the updated backend and scheduler, manually apply
`oneshot/install_scheduler_job_descriptions.sql` to install the description
column and stored-routine contracts. This one-time schema installation is
transactional and is never performed by application startup. Later description
edits require only a scheduler restart, not another SQL installation.

## Automatic Screener AI

Screener and entity analyses enable AI automatically on the server. Set
`MARKET_SCREENER_PIPELINE_LLM_ENABLED=false` in `.env` to disable it for manual
analyses and the scheduled screener pipeline (default: enabled). The analyze API
accepts only `context_type` and `context_id`; legacy `llm_enabled` values from
clients are ignored. There is no manual AI toggle in the frontend.

The existing `MARKET_ANALYSIS_ENRICHMENT_LLM_*` settings configure the provider,
model, endpoint, credentials, request timeout and retries. Missing or invalid
configuration returns `llm_enrichment.reason=invalid_configuration`; manual
analysis still computes technical signals, ranking and snapshots with `PARTIAL`
status. Endpoint HTTP errors (including 429), network errors and invalid URLs
stop subsequent AI calls for the current enrichment run after the configured
provider retries, with `reason=endpoint_unavailable`. Successful enrichments
already obtained are retained. Content-validation errors still isolate bad
signals through batch splitting. A later analysis can try the endpoint again;
this is not a persistent endpoint cooldown. Scheduled pipelines retain their
explicit `MARKET_SCREENER_PIPELINE_STOP_ON_ERROR` policy (default: false).

Set `MARKET_ANALYSIS_ENRICHMENT_MIN_INTERVAL_MINUTES=30` in `.env` to configure
the AI freshness safeguard for manual context/entity analyses and scheduled
enrichment jobs. The default is 30 minutes when omitted. Values must be
nonnegative integers; `0` disables the time-based safeguard (other candidate
eligibility rules still apply). Invalid values return `invalid_configuration`
without loading AI candidates or calling the provider.

## Temporary Development Utilities

MIP currently has a single live development instance. Periodic database dumps
record the current schema; migration utilities are not permanent runtime tools.
Keep all one-off migrations, installation SQL, fixes and their preflight/audit
files in `oneshot/`. Keep recurring operational commands in `scripts/`.
Run Python utilities from the backend root with `python -m oneshot.<module>`
using the backend virtual environment. Nothing in `oneshot/` runs automatically.
The directory can be cleaned after development; also remove or update tests and
documentation that reference the deleted migration files.

## Entity List Filters

`GET /api/entities` accepts `scope=ALL|PORTFOLIO|WATCHLIST|SIGNALS` and `query`.
The authenticated user's portfolio/watchlist membership comes from
`sp_get_user_entity_asset_match`; `SIGNALS` selects entities with active screener
opportunities, not a price-movement ranking. Search and scope are applied by
`sp_get_user_entities` before the 500-row limit. Each returned row includes
`in_portfolio`, `in_watchlist` and `has_active_signals`.
The list returns one row per entity, including entities with multiple or no
instruments. Symbol and exchange searches use an existence check across all
instruments; the legacy result columns `symbol` and `exchange` are returned as
null to preserve the function signature without duplicating entities.

Before using this endpoint on the existing development database, manually apply
`oneshot/install_entity_list_filters.sql` after the existing asset-match and
screener routines are installed. The installer matches `mip_schema.sql`; it is
not executed on startup. The legacy `sp_get_entities(integer)` is unchanged.
Reapply this installer if an earlier version is already installed.

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

The variations dialog also accepts a historical snapshot, selected directly or
opened from the historical ranking. The comparison always uses the preceding
complete successful snapshot; an incomplete selection has no comparison. Filter
by new, changed or removed entries and export the displayed rows using the native
grid CSV export. The new-entry indicators on the main grid always refer to the
latest complete comparison, independent of the historical selection.

Run history, historical ranking and signal events distinguish loading, empty and
error states and provide retry actions. Refresh reloads the current results,
latest comparison and history lists, plus selected detail, events, ranking and
historical comparison only when applicable. It never requests details without
an identifier.

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

### Contextual screener analysis

### Unified entity and universe views

The GUI exposes `/entities`, `/entities/:id`, `/universes` and `/universes/:id`.
The entity overview combines instruments and price timestamps, user portfolio
and watchlist matches, current universe memberships, news, catalysts and active
screener opportunities. Counts include all linked news and catalysts; detail
lists contain the latest 50 of each. The entity list retains its API limit of
500 rows. Raw overview data is available in the diagnostic panel.

The active screener signals header shows `last_analysis_at`, the latest completed
technical scan that actually evaluated at least one instrument of the entity,
including evaluations with no signal. Partial scans count only instruments that
were evaluated; failed scans and insufficient-history skips do not advance this
timestamp. It is independent of the overview's `observed_at` and AI enrichment.
Scan summaries now persist `evaluated_instrument_ids`; older summaries without
these IDs cannot reliably establish recency and display `N/D` until a new scan.
Reapply the overview installer below to expose the new field.

The Screener results table displays the source instrument's `last_analysis_at`
instead of its numeric ranking position, while preserving ranking order. Apply
`oneshot/install_screener_analysis_timestamp.sql` manually to update the shared
opportunity enrichment routine. The timestamp uses the same completed-evaluation
criteria as the entity overview and displays `N/D` for legacy scans without
evaluated instrument IDs.

Authenticated `GET /api/entities/{entity_id}/overview` delegates to
`sp_get_user_entity_overview` with the authenticated user ID. Review and manually
apply `oneshot/install_entity_overview.sql` after the existing entity asset-match
and screener routines. The same definition is in `mip_schema.sql`. No live
installation or startup migration is performed.

`GET /api/screener/contexts/{context_type}/{context_id}/instruments` reuses the
authorized context-membership routine; missing or foreign collections return
404. Universe instruments link directly to the unified entity overview.
Offline endpoint and installer parity checks are in
`tests/test_entity_overview.py`; these do not validate SQL execution on PostgreSQL.

### Contextual screener installation

Review and manually apply `oneshot/install_screener_contexts.sql` after the
existing screener, ranking, consensus and snapshot installers. Its routines
are also present in the authoritative `mip_schema.sql`; no startup migration is
performed. This installer has not been executed against the live database.

If contextual analysis reports no matching constraint for `ON CONFLICT`,
reapply the updated context installer manually. The context-universe upsert
targets `upper(btrim(code::text))`, matching the existing normalized unique
index; no additional unique constraint on the raw `code` column is needed.

Authenticated `GET /api/screener/contexts` lists the user's watchlists and
portfolios plus active universes, including instrument counts. Context filtering
uses `context_type` and `context_id` together; `strategy_code` and
`consensus_direction` are applied in SQL before the result limit. Watchlists
match entity instruments, portfolios match nonzero exact-instrument positions,
and universes match current members. Foreign or missing collections return 404.

The same authenticated endpoints accept `ENTITY` and `INSTRUMENT` scopes.
Entities resolve all linked instruments; instruments resolve only the exact
instrument ID, without requiring membership in a watchlist, portfolio or
universe. Missing entities or instruments return 404. Results use identity-based
membership filtering, not name searches. Reapply the updated
`oneshot/install_screener_contexts.sql` manually to enable these scopes.

The entity overview offers `Analizza entita` in its header and an `Analizza`
action on each instrument row. Both start the scan directly, with server-configured AI,
without leaving the overview. Analysis controls share their busy state across
the overview and Screener, including route changes; the frontend service also
rejects overlapping requests and releases its guard on success or failure.
Successful scans refresh the affected entity and Screener data. This frontend
guard applies to the current app session; the backend advisory lock still
protects manual requests from other tabs or clients.

`Segnali e analisi` opens `/screener?context=ENTITY:<uuid>`; direct
`/screener?context=INSTRUMENT:<uuid>` URLs are also supported. The optional
`context_name` URL parameter is a display label only. The general Screener
retains the group selector, labelled `Ambito di analisi`. Notification scopes
are unchanged; direct entity/instrument views do not expose group notification
settings.

`POST /api/screener/analyze` accepts a context type and UUID. It synchronously
analyzes 1-1000 context instruments using the configured market-data provider
and shared scan engine, committing technical results per instrument and reporting
progress before rebuilding the global ranking. It also records a screener run
and global ranking snapshot, returning `run_id` and
`ranking_snapshot_id`. Partial scans remain in history but are excluded from
complete snapshot comparisons. Later failures do not roll back results already
committed. Manual analyses do not enqueue summary notifications. A dedicated
session advisory lock protects the incremental manual scan across its commits;
overlapping analyses are rejected with 409.

Lock contention is logged by `job_runtime/lock_logging.py` for scheduler job
locks and the shared analysis lock. `Lock contention` entries include the
requester and blocking PostgreSQL connections: PID, scheduler job ID, run ID,
job name/module, connection state and wait event. Manual analyses identify their
GUI context instead of a scheduler job. Connection labels use `application_name`
and are restored before pooled session connections are reused. Existing workers
without these labels still appear by PID/application name. If the holder exits
before inspection, the log reports `released_or_not_visible`; diagnostic errors
report `unavailable` without changing the lock decision. No database installation
is required; activity visibility depends on the database user's permissions.

Existing databases must also install the incremental-analysis routines. After
backup, review and manually apply
`oneshot/install_screener_incremental_analysis.sql` using PostgreSQL tooling
with `ON_ERROR_STOP=1`, after the context and context-AI installers. It installs
the session lock, progress routines and configurable AI freshness overloads,
matching `mip_schema.sql`. It is transactional and safe to reapply; it does not
run on startup. A missing `sp_market_screener_session_analysis_lock(boolean)`
indicates that this database upgrade has not been applied. Apply this installer
last when reapplying the older context-AI installer, which replaces the legacy
candidate-selection overload.
If scheduler execution requests are installed, reapply
`oneshot/install_scheduler_execution_requests.sql` after this installer: it
replaces `sp_market_screener_context_progress` with the scheduler-aware version.
Schema dumps taken before or after that replacement contain different progress
definitions; the incremental installer intentionally retains its standalone
definition without depending on the scheduler-request columns and routines.

Scheduled and automatic jobs also preserve independently committed results:
technical scans commit each instrument, FMP sync commits each catalyst or filing,
news ingestion commits each article, news enrichment commits each entity score,
and notification writers commit each enqueue. Exchange-rate refresh commits each
currency's current and historical rates together. Persistence failures roll back
only the current unit and continue where recoverable; counters include committed
work only. Earlier commits survive even if failure reporting or a later stage
cannot complete. AI request batching is retained, but a score persistence failure
does not repeat the provider request.

Operations requiring a complete database-only view remain atomic: final quant
eligibility/ranking updates and full universe membership replacement. These do
not wrap third-party calls in one transaction across independent instruments.

Technical watchlist/portfolio/entity/instrument universes are inactive and excluded from public
context discovery. Empty or oversized contexts return 422. Offline service,
HTTP and frontend contracts are in
`tests/test_market_screener_signal_runtime_contract.py`; live SQL and interactive
GUI verification remain separate checks.

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

For pending AI enrichment, the runner installs both reviewed overloads, placing
the instrument-scoped implementation before the two-argument compatibility
wrapper. Missing, duplicate or unreviewed overload signatures are rejected.

The runner executes DDL with `no_parameters=True`, preserving literal `%` markers
inside PL/pgSQL without psycopg interpreting them as driver placeholders. A failed
upgrade rolls back its transaction; rerun the corrected runner after a failure.

Apply the migration before starting the updated backend. Restart the scheduler
with the price-refresh dispatcher enabled. Also apply
`oneshot/install_scheduler_execution_requests.sql` as described above:
`/api/market-data/prices/refresh` returns HTTP 202 with a scheduler ticket, and its
request worker subsequently populates the specialist price queue. The frontend
must be upgraded with the backend. Job locks use
a dedicated database connection throughout each execution; size the connection
pool accordingly and ensure server idle-in-transaction timeouts allow job runs.

## Shared Collection Intelligence

The three intelligence jobs acquire the deduplicated union of all nonzero
portfolio positions, watchlist instruments and current members of active
universes, across all users. `sp_get_intelligence_coverage` is independent of
subscriptions and recipient preferences. Daily Mover researches each instrument;
Upcoming and Breaking Catalyst research each entity once. Research inputs contain
market identity and evidence, not owners, collections, quantities or valuations.
Existing price-history, movement, lookback, window and batching settings remain
in effect.

Validated events and their evidence are committed to
`collection_intelligence_events` before recipient lookup and delivery filters.
Accepted counts report unique persisted events, not notifications. An event can
therefore be accepted without any subscriber or delivery. Low-confidence Breaking
events and valid Upcoming events outside the delivery window are retained without
notification. Recipient lookup or individual delivery failures do not roll back
the shared event; failures are reported in the job result. This is not a durable
delivery outbox: there is no automatic replay of saved-but-undelivered events.

Upcoming research reads legacy catalysts and shared Upcoming/Breaking events via
`sp_get_recent_intelligence_catalysts`. Equivalent history entries prefer the
shared event. History exposes only market fields and sanitized instrument
identifiers, never arbitrary stored payloads. Validated catalyst payloads persist
canonical ISO dates and integer confidence values before entering SQL history.

`CollectionEventPublisher` then projects events onto subscribed portfolios,
watchlists and universes by category: `DAILY_MOVER`, `UPCOMING_CATALYST`,
`BREAKING_CATALYST` or `NEWS`. Daily Mover also matches the exact instrument.
Collection valuation, allocation, exposure and severity belong only to this
projection. Daily Mover's estimated portfolio impact in percentage points is
`dailyChangePct * allocationPct / 100`; unavailable exposure yields `null`.
Existing severity formulas are unchanged and are not mathematically proportional
to portfolio impact. NEWS retains the Breaking Catalyst queue type with a NEWS
category in its notification context.

Collection membership is deduplicated before valuation. Missing or nonpositive
prices and unavailable FX leave market values unknown. Allocation is unavailable
for every position in a portfolio if any nonzero position cannot be valued;
missing values are not replaced with zero.

Review and validate `oneshot/install_collection_intelligence.sql` against the
current PostgreSQL schema, back up the development database, and manually install
it before running the updated APIs/jobs. It installs subscription routines, shared
coverage, shared-event persistence and research history in one transaction.
No SQL is run on startup.
Offline tests mock the database and providers; they do not certify the SQL runtime.

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

Intelligence allocations use market values converted to the portfolio's base
currency. Candidate `currency_code` still identifies the listing currency, not
the aggregate market-value currency. Watchlist entries are entity-based. Catalyst
research selects one deterministic listing per entity; Daily Mover covers every
included instrument. A preferred watchlist listing is not persisted.
Their target price therefore refers to that representative listing.

## Focused verification

### Screener membership filters

The Screener shows entity names by default and supports independent portfolio
and watchlist membership filters. Portfolio membership includes other listings
of the same entity; watchlist membership uses the authenticated user's entities.
Selecting both filters combines them with AND, before the result limit.

Review and manually apply `oneshot/install_screener_watchlist_filter.sql` with
`psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f oneshot/install_screener_watchlist_filter.sql`.
This installs an additional overload of the existing filtered-opportunities
routine. Requests without a watchlist filter continue using the original overload.
No installer is run on application startup. SQL contracts are checked offline in
`tests/test_market_screener_signal_runtime_contract.py`; database execution and
interactive layout checks remain separate manual checks.

The header theme switch persists locally, using the system preference initially.
The Jobs page keeps the selected run log above a separately scrolling job list,
with quick access to a running execution or the latest recorded execution.

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
