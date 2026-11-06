from __future__ import annotations

from types import SimpleNamespace

import pytest
from market_data.capabilities import InstrumentType
from market_data.models import InstrumentProfile
from services import entities


def test_profile_payload_can_skip_quote(monkeypatch) -> None:
    captured = {}

    class FakeMarketData:
        def get_profile(self, reference, **kwargs):
            captured["reference"] = reference
            captured.update(kwargs)
            return InstrumentProfile(
                symbol="BSX",
                name="Boston Scientific",
                instrument_type=InstrumentType.STOCK,
            )

    monkeypatch.setattr(entities, "market_data", FakeMarketData())
    reference = entities.InstrumentReference(
        symbol="BSX",
        instrument_type=InstrumentType.STOCK,
    )

    payload = entities._profile_payload(
        reference,
        full_refresh=False,
        include_quote=False,
    )

    assert payload is not None
    assert captured["reference"].instrument_type == InstrumentType.STOCK
    assert captured["include_quote"] is False


def test_local_listing_respects_search_hints():
    from routers.imports import _matches_listing_hints

    listing = {
        "exchange_code": "NASDAQ",
        "currency_code": "USD",
        "type": "STOCK",
        "isin": "US123",
    }
    assert _matches_listing_hints(
        listing, {"exchange": "nasdaq", "currency": "usd", "instrument_type": "stock"}
    )
    assert not _matches_listing_hints(listing, {"exchange": "NYSE"})
    assert not _matches_listing_hints(listing, {"currency": "EUR"})
    assert not _matches_listing_hints(listing, {"isin": "US456"})
    assert not _matches_listing_hints(None, {})


@pytest.mark.parametrize("source", ["CACHE", "LOCAL"])
def test_import_persists_profile_when_exchange_is_unavailable(
    monkeypatch, source
):
    from routers import imports

    local_payload = {
        "name": "Example Corp",
        "currency": "USD",
        "type": "STOCK",
        "entity_id": "entity-id",
        "instrument_id": "instrument-id",
        "last_price": 10 if source == "LOCAL" else None,
    }
    symbol_info = {"found": True, "source": source, "payload": local_payload}
    captured = {}

    class FakeDb:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(scalar=lambda: symbol_info)

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    class FakeMarketData:
        def get_profile(self, reference, **kwargs):
            captured["reference"] = reference
            captured.update(kwargs)
            return InstrumentProfile(
                symbol="EXM",
                name="Example Corp",
                instrument_type=InstrumentType.STOCK,
                currency="USD",
                last_price=10,
            )

    monkeypatch.setattr(imports, "SessionLocal", FakeDb)
    monkeypatch.setattr(imports, "market_data", FakeMarketData())
    monkeypatch.setattr(
        imports,
        "upsert_asset_profile",
        lambda _db, profile: captured.update(profile=profile)
        or SimpleNamespace(
            entity_id="entity-id",
            instrument_id="instrument-id",
            imported=True,
            action="CREATED",
        ),
    )

    result = imports.import_entity({"symbol": "EXM"}, current_user=None)

    assert result["imported"] is True
    assert "exchange_code" in captured["required_fields"]
    assert captured["reference"].instrument_type == InstrumentType.STOCK
    assert captured["profile"]["exchange"] is None


def test_import_persists_requested_exchange_when_provider_omits_it(monkeypatch):
    from routers import imports

    captured = {}

    class FakeDb:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(scalar=lambda: None)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class FakeMarketData:
        def get_profile(self, _reference, **kwargs):
            captured.update(kwargs)
            return InstrumentProfile(
                symbol="EXM",
                name="Example Corp",
                instrument_type=InstrumentType.STOCK,
                currency="USD",
                last_price=10,
            )

    monkeypatch.setattr(imports, "SessionLocal", FakeDb)
    monkeypatch.setattr(imports, "market_data", FakeMarketData())
    monkeypatch.setattr(
        imports,
        "upsert_asset_profile",
        lambda _db, profile: captured.update(profile=profile)
        or SimpleNamespace(
            entity_id="entity-id",
            instrument_id="instrument-id",
            imported=True,
            action="CREATED",
        ),
    )

    result = imports.import_entity(
        {"symbol": "EXM", "exchange_code": "XNAS"}, current_user=None
    )

    assert result["imported"] is True
    assert captured["profile"]["exchange"] == "XNAS"
    assert "exchange_code" not in captured["required_fields"]
