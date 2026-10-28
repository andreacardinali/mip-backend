from market_analysis.scan import (
    ScanConfig,
    ScanMember,
    ScanSkipReason,
    scan_members,
)
from market_data.models import PriceBar


class MarketDataWithShortHistory:
    def get_ohlcv(self, request):
        return [object()] * 89


def test_insufficient_history_is_skipped_not_failed() -> None:
    result = scan_members(
        [
            ScanMember(
                universe_id="universe",
                instrument_id="instrument",
                entity_id="entity",
                symbol="SPCX",
            )
        ],
        market_data_service=MarketDataWithShortHistory(),
        config=ScanConfig(minimum_bars=200),
    )

    assert result.processed == 1
    assert result.failures == ()
    assert result.candidates == ()
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == ScanSkipReason.INSUFFICIENT_HISTORY
    assert result.skipped[0].available_bars == 89
    assert result.skipped[0].required_bars == 200
    assert result.to_dict()["failures"] == 0
    assert result.to_dict()["skipped_by_reason"]["INSUFFICIENT_HISTORY"] == 1
    assert result.evaluations == ()


def test_successful_no_signal_scan_records_only_evaluated_scope(monkeypatch):
    from market_analysis.lifecycle import expire_evaluated_signals

    class MarketData:
        def get_ohlcv(self, request):
            if request.instrument.symbol == "FAIL":
                raise ValueError("provider unavailable")
            return [
                PriceBar(
                    timestamp=timestamp, open=100, high=101, low=99, close=100, volume=1
                )
                for timestamp in (200000, 100000)
            ]

    monkeypatch.setattr("market_analysis.scan._evaluate", lambda *args: ())
    result = scan_members(
        [ScanMember("u", "good", "e", "GOOD"), ScanMember("u", "bad", "e", "FAIL")],
        market_data_service=MarketData(),
        config=ScanConfig(minimum_bars=2, enabled_strategies=("SUPERTREND",)),
    )
    assert result.skipped[0].reason == ScanSkipReason.NO_TECHNICAL_SIGNAL
    assert len(result.evaluations) == 1
    assert result.evaluations[0].observed_at.timestamp() == 200000
    captured = []

    class Repository:
        def __init__(self, db):
            pass

        def expire_evaluated(self, scopes, signal_ids, stability_minutes):
            captured.extend(scopes)
            return 1

    monkeypatch.setattr(
        "market_analysis.lifecycle.SignalLifecycleRepository", Repository
    )
    assert expire_evaluated_signals(object(), result, []) == 1
    assert len(captured) == 1
    assert captured[0]["instrument_id"] == "good"
    assert captured[0]["strategy_code"] == "SUPERTREND"
    assert captured[0]["timeframe"] == "1D"
