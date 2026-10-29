"""Print the market-time migration; apply only with explicit --apply."""

import argparse
import re
from pathlib import Path


def migration_statements(schema: str) -> list[str]:
    matches = re.findall(
        r"^CREATE FUNCTION public\.sp_upsert_filing\(.*?^\$\$;",
        schema,
        flags=re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1:
        raise ValueError("Expected exactly one filing function definition")
    definition = matches[0]
    if "p_accepted_at timestamp with time zone" not in definition.splitlines()[0]:
        raise ValueError("Filing function must accept an aware timestamp")
    preflight = Path(__file__).with_name("market_time_preflight.sql")
    return [
        preflight.read_text(encoding="utf-8"),
        definition.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply using DATABASE_URL")
    args = parser.parse_args()
    schema = Path(__file__).resolve().parents[1] / "mip_schema.sql"
    statements = migration_statements(schema.read_text(encoding="utf-8"))
    if not args.apply:
        print("BEGIN;\n" + "\n\n".join(statements) + "\nCOMMIT;")
        return
    from db import engine

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(
                statement, execution_options={"no_parameters": True}
            )
    print("Market-time migration applied successfully.")


if __name__ == "__main__":
    main()
