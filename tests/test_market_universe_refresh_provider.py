from datetime import UTC, datetime

from market_analysis.universes import UniverseDefinition
from market_data.models import IndexConstituent
from services.market_universe_refresh import refresh_market_universes


class FakeMarketData:
    def get_index_constituents(self, code):
        return [
            IndexConstituent(
                index_code=code,
                symbol="AAPL",
                source="WIKIPEDIA",
                source_reference="u",
                raw_payload={},
            ),
            IndexConstituent(
                index_code=code,
                symbol="MSFT",
                source="WIKIPEDIA",
                source_reference="u",
                raw_payload={},
            ),
        ]


class FakeRepository:
    def __init__(self, db):
        pass

    def resolve_universe_symbols(self, *, symbols):
        return {symbol: f"id-{symbol}" for symbol in symbols}

    def upsert_universe(self, **kwargs):
        assert kwargs["source_system_code"] == "WIKIPEDIA"
        return "universe-id"

    def replace_universe_members(self, **kwargs):
        return {"requested": 2, "added": 2, "removed": 0}


def test_refresh_reads_constituents_via_market_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.market_universe_refresh.MarketAnalysisRepository", FakeRepository
    )
    definition = UniverseDefinition("TEST", "Test", "INDEX", 2, 2)
    result = refresh_market_universes(
        object(),
        market_data_service=FakeMarketData(),
        definitions=[definition],
        effective_at=datetime(2026, 10, 23, tzinfo=UTC),
    )
    assert result["TEST"]["members"] == 2
    assert result["TEST"]["provider"] == "WIKIPEDIA"
