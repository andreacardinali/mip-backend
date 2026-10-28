"""Synchronize Eulerpool exchange metadata and holidays; dry-run by default."""

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(
            "Would synchronize /market/exchanges, then /market/holidays/{code} "
            "for all registered exchanges. No HTTP or database access."
        )
        return
    from jobs.eulerpool_exchange_sync import run

    print(json.dumps(run(), ensure_ascii=True))


if __name__ == "__main__":
    main()