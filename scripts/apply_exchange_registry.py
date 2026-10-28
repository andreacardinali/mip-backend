"""Print exchange registry migration; connect only with explicit --apply."""

import argparse
import re
from pathlib import Path


def migration_statements(
    *, upgrade: bool = False, rename_job_only: bool = False
) -> list[str]:
    if upgrade and rename_job_only:
        raise ValueError("Choose either registry upgrade or scheduler rename only")
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    statements = ["SET LOCAL TIME ZONE 'UTC';"]
    table_patterns = (
        r"^CREATE TABLE public\.exchange_holiday_snapshots \(.*?^\);",
        r"^ALTER TABLE ONLY public\.exchange_holiday_snapshots\n.*?;",
    )
    if upgrade:
        statements.extend(
            [
                "ALTER TABLE public.exchange_holiday_snapshots "
                "DROP CONSTRAINT exchange_holiday_snapshots_exchange_code_check;",
                "ALTER TABLE public.exchange_holiday_snapshots "
                "ALTER COLUMN exchange_code TYPE character varying(20);",
                "ALTER TABLE public.exchange_holiday_snapshots "
                "ADD CONSTRAINT exchange_holiday_snapshots_exchange_code_check "
                "CHECK (exchange_code ~ '^[A-Z0-9]{4,20}$');",
            ]
        )
    registry_patterns = (() if upgrade else table_patterns) + (
        r"^CREATE FUNCTION public\.sp_store_exchange_holidays\(.*?^\$\$;",
        r"^CREATE FUNCTION public\.sp_upsert_exchange\(.*?^\$\$;",
        r"^CREATE FUNCTION public\.sp_sync_exchange_catalog\(.*?^\$\$;",
    )
    patterns = (() if rename_job_only else registry_patterns) + (
        r"^CREATE FUNCTION public\.sp_migrate_exchange_sync_job\(.*?^\$\$;",
    )
    for pattern in patterns:
        matches = re.findall(pattern, schema, flags=re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ValueError("Expected exactly one exchange registry definition")
        statements.append(
            matches[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        )
    return [*statements, "SELECT public.sp_migrate_exchange_sync_job();"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--upgrade", action="store_true", help="Upgrade an existing exchange registry"
    )
    mode.add_argument(
        "--rename-job-only",
        action="store_true",
        help="Rename the existing scheduler job without changing registry objects",
    )
    args = parser.parse_args()
    statements = migration_statements(
        upgrade=args.upgrade, rename_job_only=args.rename_job_only
    )
    if not args.apply:
        print("BEGIN;\n" + "\n\n".join(statements) + "\nCOMMIT;")
        return
    from db import engine

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(
                statement, execution_options={"no_parameters": True}
            )
    print("Exchange registry migration applied.")


if __name__ == "__main__":
    main()
