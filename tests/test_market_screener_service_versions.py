from market_screener.config import ScreenerRankingConfig
from market_screener.service import build_market_screener_ranking


class FakeRepository:
    def __init__(self):
        self.input_version = "unset"
        self.persisted_version = None

    def list_ranking_inputs(self, *, algorithm_version, limit):
        self.input_version = algorithm_version
        return []

    def replace_ranking(self, *, algorithm_version, opportunities):
        self.persisted_version = algorithm_version
        return {"upserted": len(opportunities)}


def test_signal_and_ranking_algorithm_versions_are_independent():
    repository = FakeRepository()
    result = build_market_screener_ranking(
        repository,
        config=ScreenerRankingConfig(),
        algorithm_version="market-screener-v1",
        signal_algorithm_version="market-analysis-v1",
    )
    assert repository.input_version == "market-analysis-v1"
    assert repository.persisted_version == "market-screener-v1"
    assert result["signal_algorithm_version"] == "market-analysis-v1"
