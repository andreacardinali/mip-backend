MIP shared HTTP retry component, phase 4

Purpose
-------
Centralize transport-level HTTP retry policy for LLM, FMP, Yahoo market data,
and ECB exchange rates.

Important semantics
-------------------
- max_retries means retries AFTER the initial attempt.
- retryable statuses: 408, 429, 500, 502, 503, 504.
- Retry-After is honored when present.
- backoff is exponential and bounded, with jitter.
- GET/HEAD/OPTIONS/TRACE/PUT/DELETE may retry by default.
- POST retries require retry_non_idempotent=True.
- JSON parsing and domain validation failures are not retried.

Telegram is intentionally NOT migrated. sendMessage is non-idempotent and the
notification database pipeline already owns delivery retries. Adding HTTP-level
retries there could produce duplicate messages.

New component
-------------
http_client/__init__.py
http_client/retry.py

Updated consumers
-----------------
llm/providers/gemini.py
llm/providers/ollama.py
llm/providers/openai_compatible.py
jobs/fmp/client.py
market_data/config.py
market_data/factory.py
market_data/providers/yahoo.py
providers/exchange_rates/ecb.py
jobs/exchange_rates/refresh_exchange_rates.py
scripts/refresh_exchange_rates.py

Obsolete file to delete
-----------------------
llm/retry.py

Environment additions
---------------------
MARKET_DATA_HTTP_MAX_RETRIES=3
MARKET_DATA_HTTP_RETRY_BASE_SECONDS=1
EXCHANGE_RATES_HTTP_MAX_RETRIES=3
EXCHANGE_RATES_HTTP_RETRY_BASE_SECONDS=1

FMP_HTTP_MAX_RETRIES and FMP_HTTP_RETRY_DELAY remain valid. Their semantics are
now explicit: retries after the first attempt, and exponential base delay.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-http-client-phase4.zip -d /opt/mip/mip-backend
rm -f llm/retry.py

Validation
----------
python -m compileall http_client llm jobs market_data providers scripts
python -m unittest tests.test_http_retry

grep -RIn --exclude-dir=.git --exclude-dir=__pycache__ "from llm.retry" .
The grep output must be empty.

Database
--------
No SQL changes are required.
