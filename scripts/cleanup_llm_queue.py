#!/usr/bin/env python3

import argparse
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

from config.settings import settings  # noqa: E402


@dataclass(frozen=True, slots=True)
class CleanupResult:
    completed_count: int
    failed_count: int
    abandoned_count: int
    orphaned_pending_count: int
    expired_processing_count: int
    purged_count: int

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
    ) -> "CleanupResult":
        return cls(
            completed_count=int(row["p_completed_count"]),
            failed_count=int(row["p_failed_count"]),
            abandoned_count=int(row["p_abandoned_count"]),
            orphaned_pending_count=int(row["p_orphaned_pending_count"]),
            expired_processing_count=int(row["p_expired_processing_count"]),
            purged_count=int(row["p_purged_count"]),
        )

    @property
    def total_count(self) -> int:
        if self.purged_count:
            return self.purged_count

        return (
            self.completed_count
            + self.failed_count
            + self.abandoned_count
            + self.orphaned_pending_count
            + self.expired_processing_count
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Preview or execute cleanup of the persistent LLM request queue."),
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Delete matching rows. Without this option, only a dry-run is performed."
        ),
    )

    parser.add_argument(
        "--include-orphans",
        action="store_true",
        help=(
            "Include old PENDING/RETRY requests and "
            "PROCESSING requests whose lease expired."
        ),
    )

    parser.add_argument(
        "--orphan-minutes",
        type=positive_integer,
        default=60,
        help=(
            "Minimum age in minutes for PENDING and RETRY "
            "requests to be considered orphaned. "
            "Default: 60."
        ),
    )

    parser.add_argument(
        "--queue-key",
        default=None,
        help=("Limit cleanup to one queue key, for example 'gemini:gemma-4-31b-it'."),
    )

    parser.add_argument(
        "--completed-days",
        type=positive_integer,
        default=7,
        help="Retention for COMPLETED rows. Default: 7.",
    )

    parser.add_argument(
        "--failed-days",
        type=positive_integer,
        default=30,
        help="Retention for FAILED rows. Default: 30.",
    )

    parser.add_argument(
        "--abandoned-days",
        type=positive_integer,
        default=30,
        help="Retention for ABANDONED rows. Default: 30.",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help=("Skip the interactive confirmation when used together with --execute."),
    )

    parser.add_argument(
        "--purge-all",
        action="store_true",
        help=(
            "Delete every request in the selected queue, "
            "including PENDING, RETRY and PROCESSING. "
            "Retention and lease checks are ignored."
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow a non-interactive full purge. Requires --purge-all --execute --yes."
        ),
    )

    return parser.parse_args()


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")

    return parsed


def normalize_database_url(url: str) -> str:
    """
    Convert a SQLAlchemy URL such as:

        postgresql+psycopg://...

    to a psycopg-compatible PostgreSQL URL:

        postgresql://...
    """

    parsed = urlsplit(url)

    if parsed.scheme == "postgresql+psycopg":
        parsed = parsed._replace(
            scheme="postgresql",
        )

    if parsed.scheme not in {
        "postgresql",
        "postgres",
    }:
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    return urlunsplit(parsed)


def get_database_url() -> str:
    value = str(settings.get("DATABASE_URL", "") or "").strip()

    if not value:
        raise RuntimeError("DATABASE_URL is not defined in .env")

    return normalize_database_url(value)


def describe_database(url: str) -> str:
    parsed = urlsplit(url)

    host = parsed.hostname or "<local>"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or "<default>"

    return f"{host}:{port}/{database}"


def call_cleanup(
    connection: Connection,
    *,
    completed_days: int,
    failed_days: int,
    abandoned_days: int,
    include_orphans: bool,
    orphan_age: timedelta,
    queue_key: str | None,
    dry_run: bool,
    purge_all: bool,
) -> CleanupResult:
    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            CALL public.sp_cleanup_llm_request_queue(
                p_completed_retention_days => %s,
                p_failed_retention_days => %s,
                p_abandoned_retention_days => %s,
                p_include_orphans => %s,
                p_orphan_age => %s,
                p_queue_key => %s,
                p_dry_run => %s,
                p_purge_all => %s,
                p_completed_count => %s,
                p_failed_count => %s,
                p_abandoned_count => %s,
                p_orphaned_pending_count => %s,
                p_expired_processing_count => %s,
                p_purged_count => %s
            )
            """,
            (
                completed_days,
                failed_days,
                abandoned_days,
                include_orphans,
                orphan_age,
                queue_key,
                dry_run,
                purge_all,
                0,
                0,
                0,
                0,
                0,
                0,
            ),
        )

        row = cursor.fetchone()

    if row is None:
        raise RuntimeError("Cleanup procedure returned no result")

    return CleanupResult.from_row(row)


def print_settings(
    args: argparse.Namespace,
    *,
    database_description: str,
) -> None:
    if args.purge_all:
        mode = "FULL PURGE" if args.execute else "FULL PURGE DRY RUN"
    else:
        mode = "EXECUTE" if args.execute else "DRY RUN"

    print("LLM queue cleanup")
    print(f"  mode: {mode}")
    print(f"  database: {database_description}")
    print(f"  queue key: {args.queue_key or '<all>'}")
    print(f"  completed retention: {args.completed_days} days")
    print(f"  failed retention: {args.failed_days} days")
    print(f"  abandoned retention: {args.abandoned_days} days")
    print(f"  include orphans: {args.include_orphans}")
    print(f"  orphan age: {args.orphan_minutes} minutes")
    print(f"  purge all: {args.purge_all}")
    print()


def print_result(
    result: CleanupResult,
    *,
    dry_run: bool,
    purge_all: bool,
) -> None:
    if purge_all:
        verb = "would be deleted" if dry_run else "deleted"

        print("Full purge result")
        print(f"  total rows {verb}: {result.purged_count}")
        return
    verb = "would be deleted" if dry_run else "deleted"

    print("Cleanup result")
    print(f"  COMPLETED: {result.completed_count}")
    print(f"  FAILED: {result.failed_count}")
    print(f"  ABANDONED: {result.abandoned_count}")
    print(f"  orphaned PENDING/RETRY: {result.orphaned_pending_count}")
    print(f"  expired PROCESSING: {result.expired_processing_count}")
    print(f"  total {verb}: {result.total_count}")


def confirm_execution(
    *,
    purge_all: bool,
    queue_key: str | None,
    force: bool,
) -> bool:
    if not purge_all:
        answer = input("Delete the matching queue rows? Type 'yes' to continue: ")

        return answer.strip().lower() == "yes"

    scope = queue_key if queue_key is not None else "ALL QUEUES"

    print()
    print("WARNING: FULL QUEUE PURGE")
    print("This deletes PENDING, RETRY and PROCESSING requests.")
    print("Workers currently processing requests may continue their provider calls.")
    print(f"Scope: {scope}")
    print()

    if not force:
        answer = input("Type 'PURGE LLM QUEUE' to continue: ")

        return answer == "PURGE LLM QUEUE"
    else:
        return force


def main() -> int:
    args = parse_args()

    if args.force and not args.purge_all:
        print(
            "Configuration error: --force can only be used with --purge-all.",
            file=sys.stderr,
        )
        return 2

    if args.force and not args.execute:
        print(
            "Configuration error: --force requires --execute.",
            file=sys.stderr,
        )
        return 2

    if args.purge_all and args.include_orphans:
        print(
            "Configuration error: --include-orphans is redundant with --purge-all.",
            file=sys.stderr,
        )
        return 2

    try:
        database_url = get_database_url()
    except (RuntimeError, ValueError) as exc:
        print(
            f"Configuration error: {exc}",
            file=sys.stderr,
        )
        return 2

    print_settings(
        args,
        database_description=describe_database(database_url),
    )

    if args.execute and not (args.yes and (not args.purge_all or args.force)):
        if not confirm_execution(
            purge_all=args.purge_all,
            queue_key=args.queue_key,
            force=args.force,
        ):
            print("Cleanup cancelled.")
            return 0

    try:
        with psycopg.connect(
            database_url,
            autocommit=False,
            connect_timeout=15,
            application_name=("mip-llm-queue-cleanup"),
        ) as connection:
            result = call_cleanup(
                connection,
                completed_days=args.completed_days,
                failed_days=args.failed_days,
                abandoned_days=args.abandoned_days,
                include_orphans=(args.include_orphans),
                orphan_age=timedelta(minutes=args.orphan_minutes),
                queue_key=args.queue_key,
                dry_run=not args.execute,
                purge_all=args.purge_all,
            )

            connection.commit()

    except psycopg.Error as exc:
        print(
            "Database cleanup failed:",
            file=sys.stderr,
        )
        print(
            f"  {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print_result(
        result,
        dry_run=not args.execute,
        purge_all=args.purge_all,
    )

    if not args.execute:
        print()
        print("Dry-run completed. Use --execute to delete these rows.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
