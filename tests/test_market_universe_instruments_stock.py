from __future__ import annotations

from market_data.capabilities import InstrumentType
from services.market_universe_instruments import import_universe_instrument


class NestedTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeDb:
    def begin_nested(self):
        return NestedTransaction()


def test_universe_import_forces_stock_and_skips_quote(monkeypatch) -> None:
    captured = {}

    def fake_refresh(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "SUCCESS",
            "failed": 0,
            "created": 1,
            "updated": 0,
            "instrument_id": "instrument-id",
        }

    monkeypatch.setattr(
        "services.market_universe_instruments.refresh_entities",
        fake_refresh,
    )

    assert import_universe_instrument(FakeDb(), " bsx ") is True
    assert captured["symbol"] == "BSX"
    assert captured["instrument_type"] == InstrumentType.STOCK
    assert captured["include_quote"] is False
    assert captured["create_if_missing"] is True
