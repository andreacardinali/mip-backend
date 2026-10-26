from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from market_analysis.repository import MarketAnalysisRepository
from market_analysis.universes import UniverseDefinition, validate_constituents

logger = logging.getLogger(__name__)
TransactionRunner = Callable[[Callable[[Any], Any]], Any]
InstrumentImporter = Callable[[Any, str], bool]


def refresh_market_universes_incrementally(
    *,
    market_source_service,
    definitions: Iterable[UniverseDefinition],
    run_transactional: TransactionRunner,
    instrument_importer: InstrumentImporter,
    import_limit: int = 0,
    effective_at: datetime | None = None,
) -> dict[str, Any]:
    """Refresh universes with a durable transaction for every missing instrument.

    Successful instrument imports remain committed if a later symbol or universe
    fails. Universe membership is replaced only after every constituent resolves.
    """
    if import_limit < 0:
        raise ValueError("import_limit must be >= 0")

    timestamp = effective_at or datetime.now(UTC)
    results: dict[str, Any] = {}
    remaining_budget = import_limit

    for definition in definitions:
        constituents = market_source_service.get_universe_members(definition.code)
        validate_constituents(constituents, definition)
        symbols = [item.symbol for item in constituents]

        resolved = _resolve_symbols(run_transactional, symbols)
        unresolved_before = sorted(set(symbols) - set(resolved))
        selected = unresolved_before
        if import_limit:
            selected = unresolved_before[:remaining_budget]

        imported: list[str] = []
        failed: list[str] = []
        for symbol in selected:
            succeeded = run_transactional(
                lambda db, current_symbol=symbol: instrument_importer(
                    db, current_symbol
                )
            )
            if succeeded:
                imported.append(symbol)
            else:
                failed.append(symbol)

        if import_limit:
            remaining_budget = max(0, remaining_budget - len(selected))

        resolved = _resolve_symbols(run_transactional, symbols)
        unresolved_after = sorted(set(symbols) - set(resolved))

        if unresolved_after:
            results[definition.code] = {
                "status": "INCOMPLETE",
                "members": len(symbols),
                "resolved": len(resolved),
                "missing_before": len(unresolved_before),
                "attempted": len(selected),
                "imported": len(imported),
                "failed": len(failed),
                "failed_symbols": failed,
                "remaining": len(unresolved_after),
                "remaining_symbols": unresolved_after[:20],
            }
            logger.warning(
                "Market universe refresh incomplete code=%s resolved=%s total=%s "
                "attempted=%s imported=%s failed=%s remaining=%s",
                definition.code,
                len(resolved),
                len(symbols),
                len(selected),
                len(imported),
                len(failed),
                len(unresolved_after),
            )
            continue

        persisted = run_transactional(
            lambda db, current_definition=definition, current_constituents=constituents, current_resolved=resolved: _persist_universe(
                db,
                definition=current_definition,
                constituents=current_constituents,
                resolved=current_resolved,
                effective_at=timestamp,
            )
        )
        results[definition.code] = {
            "status": "COMPLETED",
            "members": len(symbols),
            "resolved": len(resolved),
            "missing_before": len(unresolved_before),
            "attempted": len(selected),
            "imported": len(imported),
            "failed": len(failed),
            "remaining": 0,
            **persisted,
        }

    return results


def _resolve_symbols(
    run_transactional: TransactionRunner,
    symbols: list[str],
) -> dict[str, str]:
    return run_transactional(
        lambda db: MarketAnalysisRepository(db).resolve_universe_symbols(
            symbols=symbols
        )
    )


def _persist_universe(
    db,
    *,
    definition: UniverseDefinition,
    constituents,
    resolved: dict[str, str],
    effective_at: datetime,
) -> dict[str, Any]:
    repository = MarketAnalysisRepository(db)
    sources = sorted({item.source for item in constituents if item.source})
    source_system_code = sources[0] if len(sources) == 1 else None
    source_references = sorted(
        {item.source_reference for item in constituents if item.source_reference}
    )
    universe_id = repository.upsert_universe(
        code=definition.code,
        name=definition.name,
        universe_type=definition.universe_type,
        source_system_code=source_system_code,
        active=True,
        config={
            "minimum_members": definition.minimum_members,
            "maximum_members": definition.maximum_members,
            "source_references": source_references,
        },
    )
    by_symbol = {item.symbol: item for item in constituents}
    members = [
        {
            "instrument_id": resolved[symbol],
            "source_reference": by_symbol[symbol].source_reference,
            "raw_payload": by_symbol[symbol].raw_payload,
        }
        for symbol in by_symbol
    ]
    changes = repository.replace_universe_members(
        universe_id=universe_id,
        members=members,
        effective_at=effective_at.isoformat(),
    )
    return {
        "universe_id": universe_id,
        "provider": source_system_code,
        **changes,
    }
