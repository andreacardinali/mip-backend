"""Apply the reviewed function definitions from the repository's pg_dump schema."""

import argparse
import re
from pathlib import Path

FUNCTION_NAMES = (
    "position_cost_basis",
    "sp_get_portfolio_pricing_states",
    "sp_set_position_cost_basis",
    "sp_update_user_preferences",
    "sp_market_screener_list_alert_candidates",
    "sp_scheduler_try_job_lock",
    "sp_scheduler_list_job_ids",
    "sp_scheduler_defer_job_run_request",
    "sp_scheduler_recover_job_runs",
    "sp_upsert_watchlist_item",
    "sp_get_intelligence_candidates",
    "sp_get_intelligence_entity_candidates",
    "sp_get_watchlist_items",
    "sp_get_watchlist_items_with_pricing",
    "sp_get_positions_with_pricing",
    "sp_get_portfolio_summary",
    "sp_get_portfolios",
    "sp_upsert_position",
    "sp_enqueue_market_price_refresh",
)


def migration_statements(schema: str) -> list[str]:
    preflight_path = (
        Path(__file__).resolve().with_name("intelligence_fixes_preflight.sql")
    )
    statements = [preflight_path.read_text(encoding="utf-8")]
    for name in FUNCTION_NAMES:
        pattern = rf"^CREATE FUNCTION public\.{re.escape(name)}\(.*?^\$\$;"
        matches = re.findall(pattern, schema, flags=re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one pg_dump definition for {name}")
        statements.append(
            matches[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        )
    return statements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply using DATABASE_URL; otherwise print SQL only",
    )
    args = parser.parse_args()
    schema_path = Path(__file__).resolve().parents[1] / "mip_schema.sql"
    statements = migration_statements(schema_path.read_text(encoding="utf-8"))
    if not args.apply:
        print("BEGIN;\n" + "\n\n".join(statements) + "\nCOMMIT;")
        return

    from db import engine

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
    print("Intelligence fixes applied successfully.")


if __name__ == "__main__":
    main()
