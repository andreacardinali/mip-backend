from config.settings import settings
from db import SessionLocal
from http_client.retry import RetryPolicy
from providers.exchange_rates.ecb import EcbExchangeRateProvider
from services.exchange_rates import refresh_exchange_rates


def _provider():
    return EcbExchangeRateProvider(
        timeout=float(settings.get("EXCHANGE_RATES_HTTP_TIMEOUT", 30)),
        retry_policy=RetryPolicy(
            max_retries=int(settings.get("EXCHANGE_RATES_HTTP_MAX_RETRIES", 3)),
            base_delay_seconds=float(settings.get("EXCHANGE_RATES_HTTP_RETRY_BASE_SECONDS", 1.0)),
        ),
    )


def run() -> dict:
    db = SessionLocal()
    try:
        result = refresh_exchange_rates(db, provider=_provider())
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
