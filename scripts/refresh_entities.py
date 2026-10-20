import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any

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
    parser.add_argument("--create-if-missing", action="store_true")
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print progress and normalized provider metadata for every entity.",
    )
    parser.add_argument(
        "--verbose-errors",
        action="store_true",
        help="Keep full application tracebacks in addition to progress output.",
    )
    return parser.parse_args()


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _format_identifiers(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    return (
        ",".join(
            f"{key}={identifier}"
            for key, identifier in sorted(value.items())
            if identifier is not None
        )
        or "-"
    )


def _progress_printer(event: dict[str, Any]) -> None:
    event_name = str(event.get("event") or "event").upper()
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    index = event.get("index")
    total = event.get("total")
    prefix = f"[{timestamp}]"
    if index is not None and total is not None:
        prefix += f" [{index}/{total}]"

    if event_name == "ENTITY_STARTED":
        print(
            f"{prefix} START {event['symbol']} "
            f"entity={event['entity_id']} instrument={event['instrument_id']}",
            flush=True,
        )
    elif event_name == "PROVIDER_STARTED":
        print(f"{prefix} PROVIDER {event['symbol']} lookup started", flush=True)
    elif event_name == "PROFILE_RECEIVED":
        print(
            f"{prefix} PROFILE {event['symbol']} "
            f"name={_format_value(event.get('name'))} "
            f"profile_source={_format_value(event.get('data_source'))} "
            f"price_source={_format_value(event.get('price_source'))} "
            f"price={_format_value(event.get('price_available'))} "
            f"price_as_of={_format_value(event.get('price_timestamp'))} "
            f"exchange={_format_value(event.get('exchange'))} "
            f"currency={_format_value(event.get('currency'))} "
            f"type={_format_value(event.get('instrument_type'))} "
            f"identifiers={_format_identifiers(event.get('identifiers'))}",
            flush=True,
        )
    elif event_name == "DATABASE_UPDATE_STARTED":
        print(f"{prefix} DATABASE {event['symbol']} update started", flush=True)
    elif event_name == "ENTITY_UPDATED":
        print(
            f"{prefix} OK {event['symbol']} duration={event['duration_ms']}ms "
            f"updated={event['updated']} skipped={event['skipped']} "
            f"failed={event['failed']}",
            flush=True,
        )
    elif event_name == "ENTITY_SKIPPED":
        print(
            f"{prefix} SKIP {event['symbol']} reason={event['reason']} "
            f"duration={event['duration_ms']}ms",
            flush=True,
        )
    elif event_name == "ENTITY_FAILED":
        print(
            f"{prefix} FAIL {event['symbol']} type={event['error_type']} "
            f"duration={event['duration_ms']}ms error={event['error']}",
            file=sys.stderr,
            flush=True,
        )
    elif event_name == "SCAN_STARTED":
        print(
            f"{prefix} SCAN mode={event['mode']} "
            f"symbol={_format_value(event.get('symbol'))} "
            f"full_refresh={_format_value(event.get('full_refresh'))}",
            flush=True,
        )
    elif event_name == "SCAN_LOADED":
        print(f"{prefix} SCAN loaded {event['total']} entities", flush=True)
    elif event_name in {"SCAN_COMPLETED", "CREATE_COMPLETED", "CREATE_FAILED"}:
        print(
            f"{prefix} COMPLETE status={event['status']} "
            f"requested={event['requested']} updated={event['updated']} "
            f"created={event['created']} skipped={event['skipped']} "
            f"failed={event['failed']} duration={event['duration_ms']}ms",
            flush=True,
        )
    else:
        print(f"{prefix} {event_name} {json.dumps(event, default=str)}", flush=True)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose_errors else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = SessionLocal()

    try:
        result = refresh_entities(
            db,
            mode=args.mode,
            symbol=args.symbol,
            create_if_missing=args.create_if_missing,
            full_refresh=args.full_refresh,
            progress=_progress_printer if args.debug else None,
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
