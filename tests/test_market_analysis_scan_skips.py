from market_analysis.scan import (
    ScanConfig,
    ScanMember,
    ScanSkipReason,
    scan_members,
)


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
