"""Print holiday registry migration; connect only with explicit --apply."""

import argparse
import re
from pathlib import Path


def migration_statements() -> list[str]:
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = ["SET LOCAL TIME ZONE 'UTC';"]
    for pattern in (
        r"^CREATE TABLE public\.exchange_holiday_snapshots \(.*?^\);",
        r"^ALTER TABLE ONLY public\.exchange_holiday_snapshots\n.*?;",
        r"^CREATE FUNCTION public\.sp_store_exchange_holidays\(.*?^\$\$;",
    ):
        matches = re.findall(pattern, schema, flags=re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ValueError("Expected exactly one holiday registry definition")
        statements.append(matches[0])
    return statements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    statements = migration_statements()
    if not args.apply:
        print("BEGIN;\n" + "\n\n".join(statements) + "\nCOMMIT;")
        return
    from db import engine

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(
                statement, execution_options={"no_parameters": True}
            )
    print("Holiday registry migration applied.")


if __name__ == "__main__":
    main()
