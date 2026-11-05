from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.config import MarketDataConfig
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import HistoryRequest, InstrumentReference, PriceBar
from market_data.registry import ProviderRegistry
from market_data.service import MarketDataService


class Fake:
    def __init__(self, name, types, result=None, fail=False):
        self.name = name
        self.result = result
        self.fail = fail
        self.capabilities = ProviderCapabilities(
            supported=frozenset({MarketDataCapability.OHLCV}),
            instrument_types={MarketDataCapability.OHLCV: frozenset(types)},
        )

    def get_ohlcv(self, request):
        if self.fail:
            raise InstrumentNotFoundError(self.name)
        return self.result


def config(order):
    return MarketDataConfig(
        provider_orders={c: tuple(order) for c in MarketDataCapability},
        http_timeout=1,
        http_max_retries=0,
        http_retry_base_seconds=0,
    )


def test_skips_provider_without_etf_ohlcv():
    euler = Fake("eulerpool", {InstrumentType.STOCK}, [PriceBar(1, 1, 1, 1, 1)])
    yahoo = Fake("yahoo", {InstrumentType.ETF}, [PriceBar(2, 2, 2, 2, 2)])
    service = MarketDataService(
        ProviderRegistry([euler, yahoo]), config(["eulerpool", "yahoo"])
    )
    rows = service.get_ohlcv(
        HistoryRequest(
            InstrumentReference(symbol="WSPX.MI", instrument_type=InstrumentType.ETF)
        )
    )
    assert rows[0].timestamp == 2


def test_falls_back_after_not_found():
    first = Fake("first", {InstrumentType.STOCK}, fail=True)
    second = Fake("second", {InstrumentType.STOCK}, [PriceBar(3, 3, 3, 3, 3)])
    service = MarketDataService(
        ProviderRegistry([first, second]), config(["first", "second"])
    )
    rows = service.get_ohlcv(
        HistoryRequest(
            InstrumentReference(symbol="LDO.MI", instrument_type=InstrumentType.STOCK)
        )
    )
    assert rows[0].timestamp == 3


def test_ohlcv_batch_preserves_order_and_isolates_failures():
    class SymbolFake(Fake):
        def get_ohlcv(self, request):
            if request.instrument.symbol == "BAD":
                raise InstrumentNotFoundError("BAD")
            return [PriceBar(4, 4, 4, 4, 4)]

    provider = SymbolFake("first", {InstrumentType.STOCK})
    service = MarketDataService(
        ProviderRegistry([provider]), config(["first"])
    )
    results = service.get_ohlcv_batch(
        [
            HistoryRequest(
                InstrumentReference(symbol=symbol, instrument_type=InstrumentType.STOCK)
            )
            for symbol in ("GOOD", "BAD", "NEXT")
        ]
    )

    assert len(results) == 3
    assert results[0].value[0].timestamp == 4
    assert isinstance(results[1].error, Exception)
    assert results[2].value[0].timestamp == 4
