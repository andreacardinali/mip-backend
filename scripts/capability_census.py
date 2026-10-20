#!/usr/bin/env python3
# ruff: noqa: E402
"""Audit configured market-data capabilities and registered providers.

Examples:
    python scripts/capability_census.py
    python scripts/capability_census.py --json capability_census.json
    python scripts/capability_census.py --fail-on-warning
    python scripts/capability_census.py --no-json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data.capabilities import MarketDataCapability
from market_data.factory import market_data

CRITICAL_CAPABILITIES = frozenset(MarketDataCapability)
DEFAULT_JSON_PATH = Path("capability_census.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory market-data providers and validate capability routing, "
            "coverage, and fallback redundancy."
        )
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        metavar="PATH",
        help=f"write the machine-readable report to PATH (default: {DEFAULT_JSON_PATH})",
    )
    output.add_argument(
        "--no-json",
        action="store_true",
        help="do not write a JSON report",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="return exit code 1 for warnings as well as errors",
    )
    return parser.parse_args()


def _build_report() -> dict[str, Any]:
    provider_descriptions = market_data.capabilities()
    configured_orders = market_data.config.provider_orders

    providers: dict[str, dict[str, Any]] = {
        item["name"]: item["capabilities"] for item in provider_descriptions
    }
    registered_names = set(providers)
    configured_names = {name for order in configured_orders.values() for name in order}

    capability_rows: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for capability in MarketDataCapability:
        configured = list(configured_orders.get(capability, ()))
        implementing = sorted(
            name
            for name, provider_capabilities in providers.items()
            if capability.value in provider_capabilities
        )
        active = [name for name in configured if name in implementing]
        missing = [name for name in configured if name not in registered_names]
        incompatible = [
            name
            for name in configured
            if name in registered_names and name not in implementing
        ]

        capability_rows[capability.value] = {
            "configured_order": configured,
            "registered_implementations": implementing,
            "active_order": active,
            "missing_configured_providers": missing,
            "incompatible_configured_providers": incompatible,
            "redundancy": len(active),
        }

        for name in missing:
            errors.append(
                {
                    "code": "CONFIGURED_PROVIDER_NOT_REGISTERED",
                    "capability": capability.value,
                    "provider": name,
                    "message": (
                        f"{capability.value}: configured provider {name!r} "
                        "is not registered"
                    ),
                }
            )
        for name in incompatible:
            errors.append(
                {
                    "code": "CONFIGURED_PROVIDER_LACKS_CAPABILITY",
                    "capability": capability.value,
                    "provider": name,
                    "message": (
                        f"{capability.value}: configured provider {name!r} "
                        "does not declare the capability"
                    ),
                }
            )
        if not active:
            errors.append(
                {
                    "code": "CAPABILITY_WITHOUT_ACTIVE_PROVIDER",
                    "capability": capability.value,
                    "message": f"{capability.value}: no active provider",
                }
            )
        elif capability in CRITICAL_CAPABILITIES and len(active) == 1:
            warnings.append(
                {
                    "code": "SINGLE_PROVIDER_CAPABILITY",
                    "capability": capability.value,
                    "provider": active[0],
                    "message": (
                        f"{capability.value}: single active provider {active[0]!r}"
                    ),
                }
            )

    unused_registered = sorted(registered_names - configured_names)
    for name in unused_registered:
        warnings.append(
            {
                "code": "REGISTERED_PROVIDER_UNUSED",
                "provider": name,
                "message": f"registered provider {name!r} is not used by any route",
            }
        )

    provider_rows: dict[str, dict[str, Any]] = {}
    for name in sorted(registered_names | configured_names):
        declared = providers.get(name, {})
        used_by = sorted(
            capability.value
            for capability, order in configured_orders.items()
            if name in order
        )
        provider_rows[name] = {
            "registered": name in registered_names,
            "configured": name in configured_names,
            "declared_capabilities": declared,
            "configured_for": used_by,
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "capability_count": len(capability_rows),
            "registered_provider_count": len(registered_names),
            "configured_provider_count": len(configured_names),
            "warning_count": len(warnings),
            "error_count": len(errors),
        },
        "capabilities": capability_rows,
        "providers": provider_rows,
        "unused_registered_providers": unused_registered,
        "warnings": warnings,
        "errors": errors,
    }


def _print_heading(title: str) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)


def _print_report(report: dict[str, Any]) -> None:
    _print_heading("CAPABILITY CENSUS")
    for capability, row in report["capabilities"].items():
        active = ", ".join(row["active_order"]) or "NONE"
        configured = ", ".join(row["configured_order"]) or "NONE"
        print(f"{capability:20} active=[{active}] configured=[{configured}]")

    _print_heading("PROVIDER INVENTORY")
    for name, row in report["providers"].items():
        state = "registered" if row["registered"] else "not registered"
        capabilities = ", ".join(sorted(row["declared_capabilities"])) or "NONE"
        routes = ", ".join(row["configured_for"]) or "NONE"
        print(f"{name} [{state}]")
        print(f"  capabilities: {capabilities}")
        print(f"  configured for: {routes}")

    _print_heading("REDUNDANCY ANALYSIS")
    for capability, row in report["capabilities"].items():
        count = row["redundancy"]
        status = "OK" if count >= 2 else "WARN" if count == 1 else "ERROR"
        print(f"{status:5} {capability:20} ({count} active provider(s))")

    _print_heading("FINDINGS")
    if not report["errors"] and not report["warnings"]:
        print("OK: no findings")
    for finding in report["errors"]:
        print(f"ERROR {finding['code']}: {finding['message']}")
    for finding in report["warnings"]:
        print(f"WARN  {finding['code']}: {finding['message']}")

    summary = report["summary"]
    _print_heading("SUMMARY")
    print(f"Capabilities:         {summary['capability_count']}")
    print(f"Registered providers: {summary['registered_provider_count']}")
    print(f"Configured providers: {summary['configured_provider_count']}")
    print(f"Warnings:             {summary['warning_count']}")
    print(f"Errors:               {summary['error_count']}")


def _write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"JSON report written to {path}")


def main() -> int:
    args = _parse_args()
    report = _build_report()
    _print_report(report)

    if not args.no_json:
        _write_json(report, args.json)

    has_errors = bool(report["errors"])
    has_blocking_warnings = args.fail_on_warning and bool(report["warnings"])
    return 1 if has_errors or has_blocking_warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
