# RPD extension for the MIP persistent LLM queue

Apply from the backend root after making a backup.

```bash
unzip -o mip-llm-rpd-patch.zip -d .
python scripts/apply_llm_rpd_python_patch.py
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f sql/20261021_llm_persistent_queue_rpd.sql
./scripts/check.sh
```

Gemini configuration example:

```dotenv
INTELLIGENCE_LLM_REQUESTS_PER_MINUTE=15
INTELLIGENCE_LLM_TOKENS_PER_MINUTE=250000
INTELLIGENCE_LLM_REQUESTS_PER_DAY=1500
INTELLIGENCE_LLM_DAILY_RESET_TIMEZONE=America/Los_Angeles
INTELLIGENCE_LLM_RATE_LIMIT_SAFETY_FACTOR=0.90
INTELLIGENCE_LLM_PERSISTENT_QUEUE_ENABLED=true
```

`REQUESTS_PER_DAY=0` disables the daily limiter. The SQL migration uses the existing timestamp triggers and does not assign `updated_at` manually.
