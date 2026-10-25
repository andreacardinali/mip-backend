from __future__ import annotations

from jobs.market_universe_refresh import worker


def test_worker_uses_incremental_refresh(monkeypatch) -> None:
    captured = {}

    def fake_incremental_refresh(**kwargs):
        captured.update(kwargs)
        assert kwargs["instrument_importer"](object(), "AAPL") is True
        return {"SP500": {"status": "COMPLETED", "members": 500}}

    monkeypatch.setattr(
        worker,
        "refresh_market_universes_incrementally",
        fake_incremental_refresh,
    )
    monkeypatch.setattr(
        worker,
        "import_universe_instrument",
        lambda db, symbol: symbol == "AAPL",
    )
    monkeypatch.setattr(
        worker.settings,
        "get",
        lambda key, default=None: (
            25 if key == "MARKET_UNIVERSE_IMPORT_LIMIT" else default
        ),
    )

    result = worker.run()

    assert result["SP500"]["status"] == "COMPLETED"
    assert result["SP500"]["members"] == 500
    assert captured["market_data_service"] is worker.market_data
    assert captured["definitions"] is worker.DEFAULT_UNIVERSES
    assert captured["run_transactional"] is worker.run_transactional
    assert captured["instrument_importer"] is worker.import_universe_instrument
    assert captured["import_limit"] == 25
