from config.settings import settings
from http_client.retry import RetryPolicy
from job_runtime.database import run_transactional
from providers.exchange_rates.ecb import EcbExchangeRateProvider
from services.exchange_rates import refresh_exchange_rates


def _provider():
    return EcbExchangeRateProvider(
        timeout=float(settings.get("EXCHANGE_RATES_HTTP_TIMEOUT", 30)),
        retry_policy=RetryPolicy(
            max_retries=int(settings.get("EXCHANGE_RATES_HTTP_MAX_RETRIES", 3)),
            base_delay_seconds=float(
                settings.get("EXCHANGE_RATES_HTTP_RETRY_BASE_SECONDS", 1.0)
            ),
        ),
    )


def run() -> dict:
    return run_transactional(
        lambda db: refresh_exchange_rates(db, provider=_provider())
    )
