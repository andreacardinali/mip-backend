from config.settings import settings
from db import SessionLocal
from providers.exchange_rates.ecb import EcbExchangeRateProvider
from services.exchange_rates import refresh_exchange_rates


def run() -> dict:
    db = SessionLocal()
    provider = EcbExchangeRateProvider(
        timeout=float(
            settings.get("EXCHANGE_RATES_HTTP_TIMEOUT", 30)
        )
    )

    try:
        result = refresh_exchange_rates(
            db,
            provider=provider,
        )
        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
