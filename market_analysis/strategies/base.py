from __future__ import annotations

from datetime import datetime
from typing import Protocol

from market_analysis.models import SignalCandidate, TechnicalSnapshot


class SignalStrategy(Protocol):
    code: str

    def evaluate(
        self, snapshot: TechnicalSnapshot, *, timeframe: str, observed_at: datetime
    ) -> SignalCandidate | None: ...
