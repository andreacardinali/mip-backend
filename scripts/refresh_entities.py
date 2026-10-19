import argparse
import json

from db import SessionLocal
from services.entities import RefreshMode, refresh_entities


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh one or more entities")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in RefreshMode],
        default=RefreshMode.MISSING.value,
    )
    parser.add_argument("--symbol")
    parser.add_argument(
        "--create-if-missing",
        action="store_true",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()

    try:
        result = refresh_entities(
            db,
            mode=args.mode,
            symbol=args.symbol,
            create_if_missing=args.create_if_missing,
            full_refresh=args.full_refresh,
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
