from typing import Protocol

from market_data.models import PriceBar


class MarketDataProvider(Protocol):
    name: str

    def get_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> list[PriceBar]:
        ...
