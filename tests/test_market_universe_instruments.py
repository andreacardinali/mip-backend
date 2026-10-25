from __future__ import annotations

from services.market_universe_instruments import import_universe_instrument


class NestedTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeDb:
    def __init__(self):
        self.nested_transactions = 0

    def begin_nested(self):
        self.nested_transactions += 1
        return NestedTransaction()


def test_import_uses_existing_entity_service(monkeypatch) -> None:
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
    db = FakeDb()

    assert import_universe_instrument(db, " aapl ") is True
    assert captured["symbol"] == "AAPL"
    assert captured["create_if_missing"] is True
    assert captured["full_refresh"] is False
    assert db.nested_transactions == 1


def test_import_failure_is_isolated(monkeypatch) -> None:
    def fake_refresh(db, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(
        "services.market_universe_instruments.refresh_entities",
        fake_refresh,
    )

    assert import_universe_instrument(FakeDb(), "FAIL") is False


def test_import_rejects_non_persisted_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.market_universe_instruments.refresh_entities",
        lambda db, **kwargs: {
            "status": "FAILED",
            "failed": 1,
            "created": 0,
            "updated": 0,
        },
    )

    assert import_universe_instrument(FakeDb(), "MISS") is False
