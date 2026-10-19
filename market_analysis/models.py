from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    market_price: float
    previous_close: float
    daily_change_pct: float
    observed_timestamp: int
    rsi: float | None
    sma_50: float | None
    sma_200: float | None
    bollinger_band_width_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return the API/prompt-compatible representation used by MIP."""
        return {
            "marketPrice": self.market_price,
            "previousClose": self.previous_close,
            "dailyChangePct": self.daily_change_pct,
            "rsi": self.rsi,
            "sma50": self.sma_50,
            "sma200": self.sma_200,
            "bbWidth": self.bollinger_band_width_pct,
            "observedDate": self.observed_timestamp,
        }
