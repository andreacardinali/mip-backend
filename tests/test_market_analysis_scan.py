from datetime import UTC, datetime

from market_analysis.scan import ScanConfig, ScanMember, evaluate_member, scan_members
from market_data.models import PriceBar


class FakeMarketData:
    def __init__(self, *, failing_symbol=None):
        self.failing_symbol = failing_symbol
        self.requests = []

    def get_ohlcv(self, request):
        self.requests.append(request)

        if request.instrument.symbol == self.failing_symbol:
            raise RuntimeError("provider failed")

        closes = [100.0 + index * 0.1 for index in range(220)]
        closes[-15:] = [
            115,
            114,
            113,
            112,
            111,
            110,
            109,
            108,
            107,
            106,
            105,
            104,
            103,
            104,
            105,
        ]

        return [
            PriceBar(
                timestamp=1_700_000_000 + index * 86400,
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000,
            )
            for index, close in enumerate(closes)
        ]


def member(symbol="TEST"):
    return ScanMember(
        universe_id="u",
        instrument_id=f"i-{symbol}",
        entity_id=f"e-{symbol}",
        symbol=symbol,
        exchange="XNAS",
        currency_code="USD",
    )


def test_evaluate_member_uses_market_data_service() -> None:
    market_data = FakeMarketData()

    result = evaluate_member(
        member(),
        market_data_service=market_data,
        config=ScanConfig(minimum_bars=200),
        observed_at=datetime(2026, 10, 23, tzinfo=UTC),
    )

    assert len(market_data.requests) == 1

    request = market_data.requests[0]
    assert request.instrument.symbol == "TEST"
    assert request.period == "1y"
    assert request.interval == "1d"

    if result is not None:
        assert result.member.symbol == "TEST"
        assert result.scores.technical is not None


def test_scan_isolates_provider_failure() -> None:
    result = scan_members(
        [member("GOOD"), member("BAD")],
        market_data_service=FakeMarketData(failing_symbol="BAD"),
        config=ScanConfig(minimum_bars=200),
    )
    assert result.processed == 2
    assert len(result.failures) == 1
    assert result.failures[0].symbol == "BAD"
    assert result.to_dict()["evaluated_instrument_ids"] == ["i-GOOD"]
