import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_data.service import MarketDataService

logger = logging.getLogger(__name__)


def refresh_exchange_rates(
    db: Session,
    *,
    market_data_service: MarketDataService,
) -> dict[str, Any]:
    snapshot = market_data_service.get_exchange_rates()
    updated = 0
    errors: list[dict[str, str]] = []

    for currency_code, rate_to_usd in snapshot.rates_to_usd.items():
        try:
            _validate_rate(currency_code, rate_to_usd)

            with db.begin_nested():
                db.execute(
                    text(
                        """
                        SELECT public.sp_upsert_exchange_rate(
                            CAST(:currency_code AS char(3)),
                            CAST(:rate_to_usd AS numeric)
                        )
                        """
                    ),
                    {
                        "currency_code": currency_code,
                        "rate_to_usd": rate_to_usd,
                    },
                )

                db.execute(
                    text(
                        """
                        SELECT public.sp_upsert_exchange_rate_history(
                            CAST(:currency_code AS char(3)),
                            CAST(:reference_date AS date),
                            CAST(:rate_to_usd AS numeric)
                        )
                        """
                    ),
                    {
                        "currency_code": currency_code,
                        "reference_date": snapshot.reference_date,
                        "rate_to_usd": rate_to_usd,
                    },
                )

            updated += 1

        except Exception as exc:
            errors.append(
                {
                    "currency_code": currency_code,
                    "error": str(exc),
                }
            )
            logger.exception(
                "Exchange rate update failed currency=%s",
                currency_code,
            )

    if not errors:
        status = "SUCCESS"
    elif updated > 0:
        status = "PARTIAL"
    else:
        status = "FAILED"

    return {
        "status": status,
        "source": snapshot.source,
        "reference_date": snapshot.reference_date.isoformat(),
        "base_currency": snapshot.base_currency,
        "received": len(snapshot.rates_to_usd),
        "updated": updated,
        "failed": len(errors),
        "errors": errors,
    }


def _validate_rate(
    currency_code: str,
    rate_to_usd: Decimal,
) -> None:
    if len(currency_code) != 3:
        raise ValueError("Currency code must contain exactly three characters")

    if rate_to_usd <= 0:
        raise ValueError("Exchange rate must be positive")
