from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from market_analysis.repository import MarketAnalysisRepository
from market_analysis.universes import UniverseDefinition, validate_constituents

InstrumentImporter = Callable[[str], bool]


def refresh_market_universes(
    db,
    *,
    market_data_service,
    definitions: Iterable[UniverseDefinition],
    instrument_importer: InstrumentImporter | None = None,
    effective_at: datetime | None = None,
) -> dict[str, Any]:
    """Refresh validated universes through provider-agnostic market data."""
    repository = MarketAnalysisRepository(db)
    timestamp = effective_at or datetime.now(UTC)
    results: dict[str, Any] = {}

    for definition in definitions:
        constituents = market_data_service.get_index_constituents(definition.code)
        validate_constituents(constituents, definition)
        symbols = [item.symbol for item in constituents]
        resolved = repository.resolve_universe_symbols(symbols=symbols)
        unresolved = sorted(set(symbols) - set(resolved))

        imported = []
        if unresolved and instrument_importer is not None:
            for symbol in unresolved:
                if instrument_importer(symbol):
                    imported.append(symbol)
            if imported:
                resolved = repository.resolve_universe_symbols(symbols=symbols)
                unresolved = sorted(set(symbols) - set(resolved))

        if unresolved:
            raise ValueError(
                f"{definition.code}: refusing partial replacement; unresolved symbols: "
                f"{', '.join(unresolved[:20])}"
            )

        sources = sorted({item.source for item in constituents if item.source})
        source_system_code = sources[0] if len(sources) == 1 else None
        universe_id = repository.upsert_universe(
            code=definition.code,
            name=definition.name,
            universe_type=definition.universe_type,
            source_system_code=source_system_code,
            active=True,
            config={
                "minimum_members": definition.minimum_members,
                "maximum_members": definition.maximum_members,
                "source_references": sorted(
                    {
                        item.source_reference
                        for item in constituents
                        if item.source_reference
                    }
                ),
            },
        )
        by_symbol = {item.symbol: item for item in constituents}
        members = [
            {
                "instrument_id": resolved[symbol],
                "source_reference": by_symbol[symbol].source_reference,
                "raw_payload": by_symbol[symbol].raw_payload,
            }
            for symbol in symbols
        ]
        changes = repository.replace_universe_members(
            universe_id=universe_id,
            members=members,
            effective_at=timestamp.isoformat(),
        )
        results[definition.code] = {
            "universe_id": universe_id,
            "members": len(members),
            "imported": len(imported),
            "provider": source_system_code,
            **changes,
        }

    return results
