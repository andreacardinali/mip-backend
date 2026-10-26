# Persistent LLM response cache

## Architecture

The factory composes providers in this order:

```text
CachedLLMProvider
  -> PersistentQueuedProvider (optional)
     -> Gemini / Ollama / OpenAI-compatible
```

A cache hit therefore avoids both the provider request and the persistent queue.
Concurrent identical misses are coalesced through a database claim: one caller
executes the provider request while the others wait for the cached result.

## Installation

```bash
cd /opt/mip/mip-backend
unzip -o mip_llm_persistent_cache.zip
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f sql/20261026_llm_response_cache.sql
python -m pytest -q tests/test_llm_cache.py tests/test_llm_cache_config.py
```

## Configuration

Configuration is prefix-specific and applies automatically to every caller using
`LLMConfig.from_settings()` and `create_provider()`:

```env
MARKET_ANALYSIS_LLM_CACHE_ENABLED=true
MARKET_ANALYSIS_LLM_CACHE_TTL_SECONDS=86400
MARKET_ANALYSIS_LLM_CACHE_GROUNDED_TTL_SECONDS=900
MARKET_ANALYSIS_LLM_CACHE_POLL_SECONDS=0.5
MARKET_ANALYSIS_LLM_CACHE_WAIT_TIMEOUT_SECONDS=900
MARKET_ANALYSIS_LLM_CACHE_CLAIM_STALE_SECONDS=900
```

The defaults enable exact persistent caching for all root LLM consumers.
Callers can set `cache_namespace` to a prompt/domain version, override TTL per
request with `cache_ttl_seconds`, or force refresh with `cache_bypass=True`.

## Cache key

The SHA-256 key includes provider, model, prompt, temperature, max tokens,
grounded-search flag, response schema, cache namespace, and a cache format
version. Secrets are not part of the key or stored payload.

## Cleanup

```sql
SELECT public.sp_llm_delete_expired_responses(1000);
```
