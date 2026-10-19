from market_data.base import MarketDataProvider
from market_data.models import PriceBar


class MarketDataService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider

    def get_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> list[PriceBar]:
        clean_symbol = str(symbol or "").strip()
        if not clean_symbol:
            raise ValueError("Market-data symbol cannot be empty")
        if not str(period or "").strip():
            raise ValueError("Market-data period cannot be empty")
        if not str(interval or "").strip():
            raise ValueError("Market-data interval cannot be empty")

        return self.provider.get_history(
            clean_symbol,
            period=period,
            interval=interval,
        )
