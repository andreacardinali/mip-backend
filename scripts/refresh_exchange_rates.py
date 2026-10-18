import json

from config.settings import settings
from db import SessionLocal
from providers.exchange_rates.ecb import EcbExchangeRateProvider
from services.exchange_rates import refresh_exchange_rates


def main() -> int:
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
        print(json.dumps(result, indent=2, default=str))
        return 1 if result["status"] == "FAILED" else 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
