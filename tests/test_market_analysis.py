import math
import unittest

from market_analysis.technical import (
    analyze_price_bars,
    bollinger_band_width,
    rsi,
    sma,
)
from market_data.models import PriceBar


class MarketAnalysisTests(unittest.TestCase):
    def test_sma_requires_enough_values(self):
        self.assertIsNone(sma([1.0, 2.0], 3))
        self.assertEqual(sma([1.0, 2.0, 3.0], 3), 2.0)

    def test_rsi_all_gains_is_one_hundred(self):
        closes = [float(value) for value in range(1, 17)]
        self.assertEqual(rsi(closes, period=14), 100.0)

    def test_bollinger_width_matches_previous_formula(self):
        closes = [float(value) for value in range(1, 21)]
        average = sum(closes) / len(closes)
        variance = sum((value - average) ** 2 for value in closes) / len(closes)
        expected = (4.0 * math.sqrt(variance) / average) * 100.0
        self.assertAlmostEqual(
            bollinger_band_width(closes),
            expected,
        )

    def test_snapshot_preserves_existing_and_extended_payload_keys(self):
        bars = [
            PriceBar(
                timestamp=index,
                open=float(index),
                high=float(index),
                low=float(index),
                close=float(index),
                volume=100.0,
            )
            for index in range(1, 202)
        ]
        snapshot = analyze_price_bars(bars)
        self.assertIsNotNone(snapshot)
        payload = snapshot.to_dict()
        self.assertEqual(
            set(payload),
            {
                "marketPrice",
                "previousClose",
                "dailyChangePct",
                "rsi",
                "sma50",
                "sma200",
                "bbWidth",
                "observedDate",
                "atr",
                "supertrendValue",
                "supertrendDirection",
                "swingHigh",
                "swingLow",
                "changeOfCharacter",
                "breakOfStructure",
                "bullishOrderBlock",
                "bearishOrderBlock",
                "dropFromHighPct",
                "riseFromLowPct",
                "ema50",
                "bbLower",
                "bbUpper",
                "smcBias",
                "bullishOrderBlockTop",
                "bearishOrderBlockTop",
                "bullishFairValueGap",
                "bullishFairValueGapTop",
                "bearishFairValueGap",
                "bearishFairValueGapTop",
            },
        )
        self.assertEqual(payload["observedDate"], 201)

        legacy_keys = {
            "marketPrice",
            "previousClose",
            "dailyChangePct",
            "rsi",
            "sma50",
            "sma200",
            "bbWidth",
            "observedDate",
        }
        self.assertTrue(legacy_keys.issubset(payload))


if __name__ == "__main__":
    unittest.main()
