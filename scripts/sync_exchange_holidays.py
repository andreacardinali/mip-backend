"""Acquire Eulerpool holidays for a catalog code; dry-run by default."""

import argparse

from market_time.holidays import normalize_exchange_code


def acquire(provider, exchange_code):
    code = normalize_exchange_code(exchange_code)
    matches = [item for item in provider.get_exchanges() if item.code == code]
    if len(matches) != 1:
        raise ValueError("Requested code must identify exactly one catalog exchange")
    return provider.get_exchange_holidays(code)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exchange_code", type=normalize_exchange_code)
    parser.add_argument(
        "--exchange", help="Existing MIP row; defaults to exchange_code"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(
            f"Would acquire /market/holidays/{args.exchange_code} for existing row "
            f"{args.exchange or args.exchange_code}. No HTTP or database access."
        )
        return
    from config.settings import settings
    from market_data.providers.eulerpool import EulerpoolProvider

    key = settings.get("EULERPOOL_API_KEY")
    if not key:
        raise ValueError("EULERPOOL_API_KEY is required in backend configuration")
    snapshot = acquire(EulerpoolProvider(key), args.exchange_code)
    from db import SessionLocal
    from market_time.repository import SQLHolidayRepository

    with SessionLocal() as db, db.begin():
        snapshot_id = SQLHolidayRepository(db).save(
            args.exchange or args.exchange_code, snapshot
        )
    print(f"Stored snapshot {snapshot_id} for {snapshot.exchange_code}.")


if __name__ == "__main__":
    main()
