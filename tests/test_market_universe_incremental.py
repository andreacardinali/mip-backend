from __future__ import annotations

from dataclasses import dataclass

from services import market_universe_incremental as module


@dataclass
class Definition:
    code: str = "TEST"
    name: str = "Test"
    universe_type: str = "INDEX"
    minimum_members: int = 2
    maximum_members: int = 2


@dataclass
class Constituent:
    symbol: str
    universe_code: str = "TEST"
    source: str = "WIKIPEDIA"
    source_reference: str = "source"
    raw_payload: dict | None = None


class FakeMarketData:
    def get_universe_members(self, code):
        return [
            Constituent("AAPL", raw_payload={}),
            Constituent("MSFT", raw_payload={}),
        ]


def test_each_import_uses_and_commits_its_own_transaction(monkeypatch) -> None:
    catalog = {"AAPL": "id-aapl"}
    transactions = []
    imports = []

    class FakeRepository:
        def __init__(self, db):
            self.db = db

        def resolve_universe_symbols(self, *, symbols):
            return {symbol: catalog[symbol] for symbol in symbols if symbol in catalog}

        def upsert_universe(self, **kwargs):
            return "universe-id"

        def replace_universe_members(self, **kwargs):
            return {"requested": 2, "added": 2, "removed": 0}

    def transaction(operation):
        token = object()
        transactions.append(token)
        return operation(token)

    def importer(db, symbol):
        imports.append(symbol)
        catalog[symbol] = f"id-{symbol.lower()}"
        return True

    monkeypatch.setattr(module, "MarketAnalysisRepository", FakeRepository)
    monkeypatch.setattr(module, "validate_constituents", lambda items, definition: None)

    result = module.refresh_market_universes_incrementally(
        market_source_service=FakeMarketData(),
        definitions=[Definition()],
        run_transactional=transaction,
        instrument_importer=importer,
    )

    assert imports == ["MSFT"]
    assert result["TEST"]["status"] == "COMPLETED"
    assert result["TEST"]["imported"] == 1
    assert len(transactions) == 4


def test_successful_import_is_not_repeated_on_next_execution(monkeypatch) -> None:
    catalog = {}
    calls = []

    class FakeRepository:
        def __init__(self, db):
            pass

        def resolve_universe_symbols(self, *, symbols):
            return {symbol: catalog[symbol] for symbol in symbols if symbol in catalog}

    def transaction(operation):
        return operation(object())

    def importer(db, symbol):
        calls.append(symbol)
        if symbol == "AAPL":
            catalog[symbol] = "id-aapl"
            return True
        return False

    monkeypatch.setattr(module, "MarketAnalysisRepository", FakeRepository)
    monkeypatch.setattr(module, "validate_constituents", lambda items, definition: None)

    first = module.refresh_market_universes_incrementally(
        market_source_service=FakeMarketData(),
        definitions=[Definition()],
        run_transactional=transaction,
        instrument_importer=importer,
    )
    second = module.refresh_market_universes_incrementally(
        market_source_service=FakeMarketData(),
        definitions=[Definition()],
        run_transactional=transaction,
        instrument_importer=importer,
    )

    assert first["TEST"]["status"] == "INCOMPLETE"
    assert second["TEST"]["status"] == "INCOMPLETE"
    assert calls == ["AAPL", "MSFT", "MSFT"]
