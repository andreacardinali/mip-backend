from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analysis.universes import UniverseDefinition
from market_data.models import IndexConstituent
from services.market_universe_refresh import refresh_market_universes


class FakeMarketData:
    def get_index_constituents(self, index_code):
        return [
            IndexConstituent(
                index_code=index_code,
                symbol="AAPL",
                source="WIKIPEDIA",
                source_reference="https://example.invalid",
                raw_payload={"Symbol": "AAPL"},
            ),
            IndexConstituent(
                index_code=index_code,
                symbol="MSFT",
                source="WIKIPEDIA",
                source_reference="https://example.invalid",
                raw_payload={"Symbol": "MSFT"},
            ),
        ]


class FakeRepository:
    def __init__(self, db):
        self.db = db

    def resolve_universe_symbols(self, *, symbols):
        return {"AAPL": "id-aapl", "MSFT": "id-msft"}

    def upsert_universe(self, **kwargs):
        assert kwargs["source_system_code"] == "WIKIPEDIA"
        return "universe-id"

    def replace_universe_members(self, **kwargs):
        assert len(kwargs["members"]) == 2
        return {"requested": 2, "added": 2, "removed": 0}


def definition() -> UniverseDefinition:
    return UniverseDefinition(
        code="TEST",
        name="Test",
        universe_type="INDEX",
        minimum_members=2,
        maximum_members=2,
    )


def test_refresh_is_atomic_after_full_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.market_universe_refresh.MarketAnalysisRepository",
        FakeRepository,
    )
    result = refresh_market_universes(
        object(),
        market_data_service=FakeMarketData(),
        definitions=[definition()],
        effective_at=datetime(2026, 10, 23, tzinfo=UTC),
    )
    assert result["TEST"]["members"] == 2
    assert result["TEST"]["added"] == 2


class PartialRepository(FakeRepository):
    def resolve_universe_symbols(self, *, symbols):
        return {"AAPL": "id-aapl"}


def test_refresh_refuses_partial_replacement(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.market_universe_refresh.MarketAnalysisRepository",
        PartialRepository,
    )
    with pytest.raises(ValueError, match="unresolved symbols"):
        refresh_market_universes(
            object(),
            market_data_service=FakeMarketData(),
            definitions=[definition()],
        )
