"""Print reviewed exchange mappings; validate calendars before explicit --apply."""

import argparse
from pathlib import Path
from zoneinfo import ZoneInfo


def mapping_sql() -> str:
    return (
        Path(__file__)
        .with_name("market_time_exchange_mappings.sql")
        .read_text(encoding="utf-8")
    )


def validate_calendars() -> None:
    import exchange_calendars

    for calendar_name, timezone_name in (
        ("XMIL", "Europe/Rome"),
        ("XNAS", "America/New_York"),
        ("XNYS", "America/New_York"),
        ("XAMS", "Europe/Amsterdam"),
        ("XETR", "Europe/Berlin"),
        ("XLON", "Europe/London"),
    ):
        ZoneInfo(timezone_name)
        calendar = exchange_calendars.get_calendar(calendar_name)
        if str(calendar.tz) != timezone_name:
            raise ValueError(
                f"Calendar {calendar_name}: expected {timezone_name}, got {calendar.tz}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply using DATABASE_URL")
    parser.add_argument(
        "--check-calendars", action="store_true", help="Validate without connecting"
    )
    args = parser.parse_args()
    statement = mapping_sql()
    if args.apply or args.check_calendars:
        validate_calendars()
    if not args.apply:
        print("BEGIN;\n" + statement + "\nCOMMIT;")
        return
    from db import engine

    with engine.begin() as connection:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
    print("Eight reviewed exchange mappings applied. US, XBER, XDUS, XPAR unchanged.")


if __name__ == "__main__":
    main()
