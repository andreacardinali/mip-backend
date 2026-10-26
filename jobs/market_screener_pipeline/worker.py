from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from db import SessionLocal
from jobs.market_analysis_llm_enrichment.worker import run as run_llm_enrichment
from jobs.market_discovery_analysis_scan.worker import run as run_analysis_scan
from jobs.market_discovery_candidate_resolver.worker import (
    run as run_candidate_resolver,
)
from jobs.market_discovery_refresh.worker import run as run_discovery_refresh
from jobs.market_screener_ranking.worker import run as run_ranking
from market_screener.run_repository import MarketScreenerRunRepository

logger = logging.getLogger(__name__)


def _enabled(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _run_phase(name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    logger.info("Market screener pipeline phase started phase=%s", name)
    result = operation()
    logger.info(
        "Market screener pipeline phase completed phase=%s status=%s",
        name,
        result.get("status"),
    )
    return result


def run() -> dict[str, Any]:
    algorithm_version = os.getenv(
        "MARKET_SCREENER_ALGORITHM_VERSION", "market-screener-v1"
    )
    db = SessionLocal()
    try:
        run_id = MarketScreenerRunRepository(db).start(algorithm_version)
        db.commit()
    finally:
        db.close()

    phases: dict[str, Any] = {}
    discovery_snapshot_id: str | None = None
    scan_run_id: str | None = None
    terminal_status = "SUCCESS"
    error_message: str | None = None

    phase_specs: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
        (
            "discovery_refresh",
            "MARKET_SCREENER_PIPELINE_DISCOVERY_ENABLED",
            run_discovery_refresh,
        ),
        (
            "candidate_resolver",
            "MARKET_SCREENER_PIPELINE_RESOLVER_ENABLED",
            run_candidate_resolver,
        ),
        ("analysis_scan", "MARKET_SCREENER_PIPELINE_SCAN_ENABLED", run_analysis_scan),
        ("llm_enrichment", "MARKET_SCREENER_PIPELINE_LLM_ENABLED", run_llm_enrichment),
        ("ranking", "MARKET_SCREENER_PIPELINE_RANKING_ENABLED", run_ranking),
    ]

    try:
        for phase_name, setting_name, operation in phase_specs:
            if not _enabled(setting_name):
                phases[phase_name] = {"status": "SKIPPED", "reason": "disabled"}
                continue
            try:
                result = _run_phase(phase_name, operation)
                phases[phase_name] = result
                if phase_name == "discovery_refresh":
                    discovery_snapshot_id = result.get("snapshot_id")
                elif phase_name == "analysis_scan":
                    scan_run_id = result.get("run_id")
                if result.get("status") in {"FAILED", "PARTIAL"}:
                    terminal_status = "PARTIAL"
                    if _enabled("MARKET_SCREENER_PIPELINE_STOP_ON_ERROR", False):
                        break
            except Exception as exc:
                logger.exception(
                    "Market screener pipeline phase failed phase=%s", phase_name
                )
                phases[phase_name] = {"status": "FAILED", "error": str(exc)}
                terminal_status = "PARTIAL"
                error_message = str(exc)
                if _enabled("MARKET_SCREENER_PIPELINE_STOP_ON_ERROR", False):
                    break

        db = SessionLocal()
        try:
            repository = MarketScreenerRunRepository(db)
            snapshot_id = repository.create_snapshot(
                run_id=run_id,
                algorithm_version=algorithm_version,
            )
            repository.complete(
                run_id=run_id,
                status=terminal_status,
                discovery_snapshot_id=discovery_snapshot_id,
                scan_run_id=scan_run_id,
                phase_results=phases,
                error_message=error_message,
            )
            db.commit()
        finally:
            db.close()

        result = {
            "status": terminal_status,
            "screener_run_id": str(run_id),
            "ranking_snapshot_id": str(snapshot_id),
            "discovery_snapshot_id": discovery_snapshot_id,
            "scan_run_id": scan_run_id,
            "phases": phases,
        }
        logger.info(
            "Market screener pipeline completed summary=%s",
            {
                "status": terminal_status,
                "screener_run_id": str(run_id),
                "ranking_snapshot_id": str(snapshot_id),
            },
        )
        return result
    except Exception as exc:
        db = SessionLocal()
        try:
            MarketScreenerRunRepository(db).complete(
                run_id=run_id,
                status="FAILED",
                discovery_snapshot_id=discovery_snapshot_id,
                scan_run_id=scan_run_id,
                phase_results=phases,
                error_message=str(exc),
            )
            db.commit()
        finally:
            db.close()
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
