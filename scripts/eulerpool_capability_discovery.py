#!/usr/bin/env python3
# ruff: noqa: E402
"""Discover the Eulerpool SDK surface and sample selected intelligence endpoints.

Examples:
    python scripts/eulerpool_capability_discovery.py
    python scripts/eulerpool_capability_discovery.py --show-methods
    python scripts/eulerpool_capability_discovery.py --sample
    python scripts/eulerpool_capability_discovery.py --sample --sample-symbol AAPL \
        --sample-etf IE00BK5BQT80

The inventory phase performs local SDK introspection only. The sampling phase makes
live API requests and requires EULERPOOL_API_KEY, either in the environment or in
MIP settings.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data.capabilities import MarketDataCapability

DEFAULT_DISCOVERY_FILE = Path("eulerpool_capability_discovery.json")
DEFAULT_CAPABILITY_FILE = Path("eulerpool_capability_inventory.json")
DEFAULT_METHOD_FILE = Path("eulerpool_method_inventory.json")
DEFAULT_SAMPLE_DIR = Path("artifacts/eulerpool")

# Explicit path rules avoid the false positives produced by broad keyword matching.
# A method can intentionally belong to more than one candidate capability only when
# it is present in more than one rule set.
CAPABILITY_RULES: dict[str, tuple[str, ...]] = {
    "NEWS": (
        "client.news.",
        "client.research.news",
        "client.datasets.news",
        "client.equity.grade_news",
        "client.equity.price_target_news",
        "client.equity.price_target_news_latest",
        "client.equity_extended.market_news",
    ),
    "ETF_HOLDINGS": (
        "client.etf.holdings",
        "client.etf.sectors",
        "client.etf.countries",
        "client.etf.flows",
        "client.equity.etf_exposure",
    ),
    "EARNINGS_CALENDAR": (
        "client.analytics.earnings_calendar",
        "client.calendar.earnings",
        "client.calendar.earnings_by_symbol",
        "client.calendar.earnings_surprises",
        "client.equity_extended.earnings_calendar",
    ),
    "ECONOMIC_CALENDAR": (
        "client.calendar.economic_calendar",
        "client.calendar.economic_calendar_history",
        "client.macro.calendar",
        "client.macro.calendar_properties",
    ),
    "TRANSCRIPTS": (
        "client.transcripts.",
        "client.earning_calls.",
    ),
    "SENTIMENT": (
        "client.sentiment.",
        "client.alternative.google_trends",
        "client.alternative.stocktwits",
        "client.crypto_extended.fear_greed_history",
        "client.trends.ticker_trends",
    ),
    "OWNERSHIP": (
        "client.alternative.congress_trading",
        "client.alternative.superinvestors_",
        "client.equity.beneficial_ownership",
        "client.equity.country_insider_trades",
        "client.equity.insider_trades",
        "client.equity.insider_trades_derivatives",
        "client.equity.insider_trades_eu",
        "client.equity.ownership",
        "client.equity.sec_form4",
        "client.institutional.",
        "client.singapore.insider_trades",
    ),
}

PRIORITY: dict[str, int] = {
    "NEWS": 100,
    "ETF_HOLDINGS": 95,
    "EARNINGS_CALENDAR": 90,
    "ECONOMIC_CALENDAR": 85,
    "TRANSCRIPTS": 80,
    "SENTIMENT": 75,
    "OWNERSHIP": 70,
}

CAPABILITY_TO_MIP_VALUE: dict[str, str] = {
    "NEWS": "news",
    "ETF_HOLDINGS": "etf_holdings",
    "EARNINGS_CALENDAR": "earnings_calendar",
    "ECONOMIC_CALENDAR": "economic_calendar",
    "TRANSCRIPTS": "transcripts",
    "SENTIMENT": "sentiment",
    "OWNERSHIP": "ownership",
}

SampleCall = Callable[[Any, Mapping[str, str]], Any]


def _sample_targets() -> dict[str, tuple[tuple[str, SampleCall], ...]]:
    return {
        "NEWS": (
            ("client.news.feed", lambda client, ctx: client.news.feed(limit=5)),
            (
                "client.research.news",
                lambda client, ctx: client.research.news(ctx["symbol"], limit=5),
            ),
        ),
        "ETF_HOLDINGS": (
            (
                "client.etf.holdings",
                lambda client, ctx: client.etf.holdings(ctx["etf"]),
            ),
            (
                "client.etf.sectors",
                lambda client, ctx: client.etf.sectors(ctx["etf"]),
            ),
            (
                "client.etf.countries",
                lambda client, ctx: client.etf.countries(ctx["etf"]),
            ),
        ),
        "EARNINGS_CALENDAR": (
            (
                "client.calendar.earnings",
                lambda client, ctx: client.calendar.earnings(ctx["today"]),
            ),
            (
                "client.calendar.earnings_by_symbol",
                lambda client, ctx: client.calendar.earnings_by_symbol(ctx["symbol"]),
            ),
        ),
        "ECONOMIC_CALENDAR": (
            (
                "client.calendar.economic_calendar",
                lambda client, ctx: client.calendar.economic_calendar(limit=5),
            ),
        ),
        "TRANSCRIPTS": (
            (
                "client.transcripts.search",
                lambda client, ctx: client.transcripts.search(
                    ticker=ctx["symbol"], limit=5
                ),
            ),
            (
                "client.earning_calls.list",
                lambda client, ctx: client.earning_calls.list(ctx["symbol"], limit=5),
            ),
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory the Eulerpool SDK and optionally sample live endpoints."
    )
    parser.add_argument(
        "--show-methods",
        action="store_true",
        help="print methods grouped by candidate MIP capability",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="call the curated live endpoint sample set",
    )
    parser.add_argument("--sample-symbol", default="AAPL")
    parser.add_argument("--sample-etf", default="IE00BK5BQT80")
    parser.add_argument(
        "--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR, metavar="PATH"
    )
    parser.add_argument(
        "--discovery-output",
        type=Path,
        default=DEFAULT_DISCOVERY_FILE,
        metavar="PATH",
    )
    parser.add_argument(
        "--capability-output",
        type=Path,
        default=DEFAULT_CAPABILITY_FILE,
        metavar="PATH",
    )
    parser.add_argument(
        "--method-output",
        type=Path,
        default=DEFAULT_METHOD_FILE,
        metavar="PATH",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="maximum recursive SDK namespace depth (default: 3)",
    )
    parser.add_argument(
        "--max-sample-items",
        type=int,
        default=5,
        help="maximum items retained at each list level (default: 5)",
    )
    parser.add_argument(
        "--max-sample-fields",
        type=int,
        default=50,
        help="maximum fields retained at each object level (default: 50)",
    )
    parser.add_argument(
        "--strict-sample",
        action="store_true",
        help="return exit code 1 if any sample call fails",
    )
    args = parser.parse_args()
    if args.max_depth < 1:
        parser.error("--max-depth must be at least 1")
    if args.max_sample_items < 1:
        parser.error("--max-sample-items must be at least 1")
    if args.max_sample_fields < 1:
        parser.error("--max-sample-fields must be at least 1")
    return args


def import_sdk() -> tuple[type[Any], str | None]:
    try:
        import eulerpool
        from eulerpool import Eulerpool
    except ImportError as error:
        raise SystemExit(
            "Eulerpool SDK not installed. Add eulerpool==1.1.0 to requirements.txt "
            "and install the requirements."
        ) from error
    return Eulerpool, getattr(eulerpool, "__version__", None)


def read_api_key() -> str | None:
    key = os.getenv("EULERPOOL_API_KEY")
    if key:
        return key
    try:
        from config.settings import settings

        configured = settings.get("EULERPOOL_API_KEY")
        return str(configured) if configured else None
    except Exception:
        return None


def create_client(*, require_key: bool) -> Any:
    Eulerpool, _ = import_sdk()
    key = read_api_key()
    if require_key and not key:
        raise SystemExit(
            "--sample requires EULERPOOL_API_KEY in the environment or MIP settings."
        )
    return Eulerpool(key or "inventory-only")


def safe_signature(member: Any) -> str:
    try:
        return str(inspect.signature(member))
    except (TypeError, ValueError):
        return "()"


def safe_doc(member: Any) -> str:
    try:
        return inspect.getdoc(member) or ""
    except Exception:
        return ""


def is_namespace_candidate(member: Any) -> bool:
    return not (
        member is None
        or inspect.isbuiltin(member)
        or inspect.ismodule(member)
        or inspect.isclass(member)
        or isinstance(member, (str, bytes, int, float, bool, Path))
    )


def walk_namespace(
    obj: Any,
    path: str,
    *,
    visited: set[int],
    catalog: dict[str, list[dict[str, str]]],
    depth: int,
    max_depth: int,
) -> None:
    if depth > max_depth or id(obj) in visited:
        return
    visited.add(id(obj))

    methods: list[dict[str, str]] = []
    children: list[tuple[str, Any]] = []
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        try:
            member = getattr(obj, name)
        except Exception:
            continue
        if callable(member):
            methods.append(
                {
                    "name": name,
                    "path": f"{path}.{name}",
                    "signature": safe_signature(member),
                    "doc": safe_doc(member),
                }
            )
        elif depth < max_depth and is_namespace_candidate(member):
            children.append((name, member))

    if methods:
        catalog[path] = methods

    for child_name, child in children:
        walk_namespace(
            child,
            f"{path}.{child_name}",
            visited=visited,
            catalog=catalog,
            depth=depth + 1,
            max_depth=max_depth,
        )


def discover_sdk(max_depth: int) -> tuple[dict[str, list[dict[str, str]]], str | None]:
    client = create_client(require_key=False)
    _, sdk_version = import_sdk()
    catalog: dict[str, list[dict[str, str]]] = {}
    try:
        walk_namespace(
            client,
            "client",
            visited=set(),
            catalog=catalog,
            depth=0,
            max_depth=max_depth,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return catalog, sdk_version


def flatten_methods(
    catalog: Mapping[str, Sequence[Mapping[str, str]]],
) -> list[dict[str, str]]:
    methods = [dict(method) for group in catalog.values() for method in group]
    return sorted(methods, key=lambda item: item["path"])


def path_matches(path: str, rule: str) -> bool:
    return path == rule or (rule.endswith(".") and path.startswith(rule))


def classify_methods(
    methods: Sequence[Mapping[str, str]],
) -> dict[str, list[dict[str, str]]]:
    classified: dict[str, list[dict[str, str]]] = {}
    for capability, rules in CAPABILITY_RULES.items():
        matches = [
            dict(method)
            for method in methods
            if any(path_matches(method["path"], rule) for rule in rules)
        ]
        classified[capability] = sorted(matches, key=lambda item: item["path"])
    return classified


def implemented_capabilities() -> list[str]:
    return sorted(capability.value for capability in MarketDataCapability)


def build_capability_inventory(
    classified: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, dict[str, Any]]:
    implemented = set(implemented_capabilities())
    inventory: dict[str, dict[str, Any]] = {}
    for capability, methods in classified.items():
        mip_value = CAPABILITY_TO_MIP_VALUE[capability]
        inventory[capability] = {
            "mip_value": mip_value,
            "implemented_in_mip": mip_value in implemented,
            "priority": PRIORITY[capability],
            "method_count": len(methods),
            "methods": list(methods),
        }
    return inventory


def build_roadmap(inventory: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {
            "capability": name,
            "mip_value": data["mip_value"],
            "priority": data["priority"],
            "method_count": data["method_count"],
        }
        for name, data in inventory.items()
        if not data["implemented_in_mip"] and data["method_count"] > 0
    ]
    return sorted(result, key=lambda item: (-item["priority"], item["capability"]))


def trim_payload(
    value: Any,
    *,
    max_items: int,
    max_fields: int,
    depth: int = 0,
    max_depth: int = 8,
) -> Any:
    if depth >= max_depth:
        return "<max-depth-reached>"
    if isinstance(value, Mapping):
        entries = list(value.items())
        result = {
            str(key): trim_payload(
                item,
                max_items=max_items,
                max_fields=max_fields,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for key, item in entries[:max_fields]
        }
        if len(entries) > max_fields:
            result["_truncated_fields"] = len(entries) - max_fields
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        result = [
            trim_payload(
                item,
                max_items=max_items,
                max_fields=max_fields,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            result.append({"_truncated_items": len(items) - max_items})
        return result
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return trim_payload(
            value.model_dump(),
            max_items=max_items,
            max_fields=max_fields,
            depth=depth + 1,
            max_depth=max_depth,
        )
    if hasattr(value, "__dict__"):
        return trim_payload(
            vars(value),
            max_items=max_items,
            max_fields=max_fields,
            depth=depth + 1,
            max_depth=max_depth,
        )
    return str(value)


def payload_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {"type": "object", "field_count": len(value), "fields": list(value)[:50]}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        first = value[0] if value else None
        return {
            "type": "array",
            "item_count": len(value),
            "first_item_type": type(first).__name__ if first is not None else None,
            "first_item_fields": list(first)[:50] if isinstance(first, Mapping) else [],
        }
    return {"type": type(value).__name__}


def safe_filename(endpoint: str) -> str:
    return endpoint.removeprefix("client.").replace(".", "_") + ".json"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def run_samples(args: argparse.Namespace, sdk_version: str | None) -> dict[str, Any]:
    client = create_client(require_key=True)
    context = {
        "symbol": args.sample_symbol,
        "etf": args.sample_etf,
        "today": date.today().isoformat(),
    }
    index: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sdk_version": sdk_version,
        "context": context,
        "samples": {},
    }
    try:
        for capability, targets in _sample_targets().items():
            for endpoint, invoke in targets:
                started = datetime.now(UTC)
                record: dict[str, Any] = {"capability": capability}
                try:
                    payload = invoke(client, context)
                    trimmed = trim_payload(
                        payload,
                        max_items=args.max_sample_items,
                        max_fields=args.max_sample_fields,
                    )
                    filename = safe_filename(endpoint)
                    write_json(args.sample_dir / filename, trimmed)
                    record.update(
                        {
                            "status": "OK",
                            "file": filename,
                            "shape": payload_shape(payload),
                        }
                    )
                except Exception as error:
                    record.update(
                        {
                            "status": "FAILED",
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )
                record["duration_ms"] = round(
                    (datetime.now(UTC) - started).total_seconds() * 1000, 2
                )
                index["samples"][endpoint] = record
                print(f"{record['status']:6} {endpoint}")
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    statuses = [item["status"] for item in index["samples"].values()]
    index["summary"] = {
        "total": len(statuses),
        "succeeded": statuses.count("OK"),
        "failed": statuses.count("FAILED"),
    }
    write_json(args.sample_dir / "sample_index.json", index)
    return index


def print_report(
    catalog: Mapping[str, Sequence[Mapping[str, str]]],
    inventory: Mapping[str, Mapping[str, Any]],
    roadmap: Sequence[Mapping[str, Any]],
    *,
    show_methods: bool,
) -> None:
    print("\n" + "=" * 80)
    print("EULERPOOL SDK INVENTORY")
    print("=" * 80)
    print(f"\nNamespaces discovered: {len(catalog)}")
    print("\nIMPLEMENTED CAPABILITIES")
    print("-" * 80)
    for capability in implemented_capabilities():
        print(capability)

    print("\nDISCOVERED INTELLIGENCE CAPABILITIES")
    print("-" * 80)
    for capability, data in inventory.items():
        print(f"{capability:25}{data['method_count']} methods")

    print("\nROADMAP")
    print("-" * 80)
    for item in roadmap:
        print(
            f"{item['capability']:25}priority={item['priority']} "
            f"methods={item['method_count']}"
        )

    print("\nMISSING IN MIP")
    print("-" * 80)
    for item in sorted(roadmap, key=lambda row: row["capability"]):
        print(item["capability"])

    if show_methods:
        print("\n" + "=" * 80)
        print("METHOD INVENTORY")
        print("=" * 80)
        for capability, data in inventory.items():
            print(f"\n{capability}\n" + "-" * 80)
            for method in data["methods"]:
                print(f"{method['path']}{method['signature']}")


def main() -> int:
    args = parse_args()
    catalog, sdk_version = discover_sdk(args.max_depth)
    methods = flatten_methods(catalog)
    classified = classify_methods(methods)
    inventory = build_capability_inventory(classified)
    roadmap = build_roadmap(inventory)

    method_report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sdk_version": sdk_version,
        "namespace_count": len(catalog),
        "method_count": len(methods),
        "namespaces": catalog,
        "methods": methods,
    }
    discovery_report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sdk_version": sdk_version,
        "implemented_capabilities": implemented_capabilities(),
        "candidate_capabilities": inventory,
        "roadmap": roadmap,
    }

    write_json(args.method_output, method_report)
    write_json(args.capability_output, inventory)
    write_json(args.discovery_output, discovery_report)
    print_report(catalog, inventory, roadmap, show_methods=args.show_methods)

    print(f"\nDiscovery written: {args.discovery_output}")
    print(f"Inventory written: {args.capability_output}")
    print(f"Methods written:   {args.method_output}")

    sample_report: dict[str, Any] | None = None
    if args.sample:
        print("\n" + "=" * 80)
        print("LIVE SAMPLING")
        print("=" * 80)
        sample_report = run_samples(args, sdk_version)
        summary = sample_report["summary"]
        print(
            f"\nSamples: {summary['succeeded']} succeeded, {summary['failed']} failed"
        )
        print(f"Sample index written: {args.sample_dir / 'sample_index.json'}")

    if args.strict_sample and sample_report and sample_report["summary"]["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
