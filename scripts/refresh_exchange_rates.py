import json

from db import SessionLocal
from market_data.factory import market_data
from services.exchange_rates import refresh_exchange_rates


def main() -> int:
    db = SessionLocal()
    try:
        result = refresh_exchange_rates(
            db,
            market_data_service=market_data,
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
