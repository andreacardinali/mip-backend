# MIP Portfolio Intelligence

The package replaces the n8n workflow with three independent scheduler jobs. Candidates are loaded exclusively from MIP PostgreSQL entities referenced by at least one portfolio position or watchlist item. Ghostfolio is not used.

## Install

Copy `jobs/` and `sql/` to the backend root. Merge `env.portfolio_intelligence.example` into `.env`.

```bash
psql "$DATABASE_URL" -f sql/001_portfolio_intelligence.sql
psql "$DATABASE_URL" -f sql/002_register_portfolio_intelligence_jobs.sql
```

Start in dry-run mode:

```dotenv
INTELLIGENCE_SEND_NOTIFICATIONS=false
```

## Manual tests

```bash
python -m jobs.portfolio_intelligence.upcoming_catalyst
python -m jobs.portfolio_intelligence.breaking_catalyst
python -m jobs.portfolio_intelligence.daily_mover
python -m jobs.portfolio_intelligence.job
```

Inspect `notifications`, now extended with `user_id`, `notification_key`, branch, status and JSON context. Each user is processed independently. Dry runs are stored with status `DRY_RUN`; exact deduplication applies only to `SENT` rows. After validation set `INTELLIGENCE_SEND_NOTIFICATIONS=true`.

## Branches

- Daily Movers: Yahoo OHLC, deterministic indicators and threshold, Gemini grounded research, Ollama final validation.
- Upcoming Catalysts: all MIP portfolio/watchlist entities, recent database catalysts, Gemini grounded search.
- Breaking Catalysts: all MIP portfolio/watchlist entities, Gemini grounded search for newly public material events.

The scheduler wrapper created for FMP Daily Sync is required so branch results are stored in `scheduler_job_runs.result`.

## Multi-user delivery

The same entity is evaluated separately for every owning user. Allocation and deduplication are user-scoped. Optional per-user Telegram settings are read from `user_sources` where `source_type = 'TELEGRAM'` and `config` contains `bot_token` and `chat_id`; global environment values remain the fallback.
