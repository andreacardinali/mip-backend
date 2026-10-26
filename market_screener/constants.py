from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------------
# Algorithm versions
# ---------------------------------------------------------------------------

# Versione del motore Market Analysis che produce i segnali tecnici.
# Deve essere usata anche come filtro degli input accettati dallo Screener.
MARKET_ANALYSIS_ALGORITHM_VERSION: Final[str] = (
    "market-analysis-v2-multistrategy"
)

# Alias esplicito per chiarire il contratto tra Market Analysis e Screener.
MARKET_SCREENER_SIGNAL_ALGORITHM_VERSION: Final[str] = (
    MARKET_ANALYSIS_ALGORITHM_VERSION
)

# Versione della formula unica di ranking.
# Non è configurabile tramite variabili d'ambiente.
# Va modificata soltanto quando cambia il significato del composite score.
MARKET_SCREENER_ALGORITHM_VERSION: Final[str] = (
    "market-screener-consensus-v1"
)


# ---------------------------------------------------------------------------
# Score boundaries
# ---------------------------------------------------------------------------

SCORE_MINIMUM: Final[float] = 0.0
SCORE_MAXIMUM: Final[float] = 100.0


# ---------------------------------------------------------------------------
# Ranking weights
# ---------------------------------------------------------------------------

# Formula base:
#
# composite_score =
#     attention_score * 0.20
#     + technical_score * 0.25
#     + quant_score * 0.35
#     + llm_score * 0.20

ATTENTION_WEIGHT: Final[float] = 0.20
TECHNICAL_WEIGHT: Final[float] = 0.25
QUANT_WEIGHT: Final[float] = 0.35
LLM_WEIGHT: Final[float] = 0.20

RANKING_WEIGHT_TOTAL: Final[float] = (
    ATTENTION_WEIGHT
    + TECHNICAL_WEIGHT
    + QUANT_WEIGHT
    + LLM_WEIGHT
)


# ---------------------------------------------------------------------------
# Required ranking components
# ---------------------------------------------------------------------------

# Technical e Quant sono necessari per produrre un'opportunità operativa.
# Un segnale privo di una di queste componenti non entra nel ranking.
REQUIRE_TECHNICAL_SCORE: Final[bool] = True
REQUIRE_QUANT_SCORE: Final[bool] = True


# ---------------------------------------------------------------------------
# Neutral values for optional components
# ---------------------------------------------------------------------------

# Un titolo non proveniente da una sorgente Discovery non deve ottenere
# né un bonus né una penalizzazione social.
NEUTRAL_ATTENTION_SCORE: Final[float] = 50.0

# Un segnale non ancora arricchito dall'LLM rimane classificabile con
# un contributo neutrale. Il ranking verrà ricostruito dopo l'enrichment.
NEUTRAL_LLM_SCORE: Final[float] = 50.0


# ---------------------------------------------------------------------------
# Multi-strategy consensus adjustments
# ---------------------------------------------------------------------------

# Il consenso viene applicato soltanto quando almeno due strategie
# indipendenti hanno valutato lo stesso strumento e timeframe.
MINIMUM_STRATEGIES_FOR_CONSENSUS: Final[int] = 2

# Bonus massimo assegnato alla confluenza direzionale completa.
CONSENSUS_BONUS_MAXIMUM: Final[float] = 8.0

# Penalità massima assegnata a un conflitto direzionale completo.
CONFLICT_PENALTY_MAXIMUM: Final[float] = 12.0


# ---------------------------------------------------------------------------
# Operational ranking limits
# ---------------------------------------------------------------------------

# Soglia minima del composite score per entrare nel ranking.
MARKET_SCREENER_MINIMUM_SCORE: Final[float] = 0.0

# Numero massimo di opportunità persistite nella classifica corrente.
MARKET_SCREENER_LIMIT: Final[int] = 50

# La query degli input carica più segnali del limite finale, perché il
# consolidamento multi-strategia può aggregare più righe nello stesso titolo.
RANKING_INPUT_LIMIT_MULTIPLIER: Final[int] = 8
RANKING_INPUT_MINIMUM_LIMIT: Final[int] = 200


# ---------------------------------------------------------------------------
# Consensus directions
# ---------------------------------------------------------------------------

CONSENSUS_DIRECTION_LONG: Final[str] = "LONG"
CONSENSUS_DIRECTION_SHORT: Final[str] = "SHORT"
CONSENSUS_DIRECTION_CONFLICT: Final[str] = "CONFLICT"
CONSENSUS_DIRECTION_NEUTRAL: Final[str] = "NEUTRAL"

CONSENSUS_DIRECTIONS: Final[frozenset[str]] = frozenset(
    {
        CONSENSUS_DIRECTION_LONG,
        CONSENSUS_DIRECTION_SHORT,
        CONSENSUS_DIRECTION_CONFLICT,
        CONSENSUS_DIRECTION_NEUTRAL,
    }
)


# ---------------------------------------------------------------------------
# Signal directions
# ---------------------------------------------------------------------------

SIGNAL_DIRECTION_LONG: Final[str] = "LONG"
SIGNAL_DIRECTION_SHORT: Final[str] = "SHORT"

SIGNAL_DIRECTIONS: Final[frozenset[str]] = frozenset(
    {
        SIGNAL_DIRECTION_LONG,
        SIGNAL_DIRECTION_SHORT,
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ATTENTION_WEIGHT",
    "CONFLICT_PENALTY_MAXIMUM",
    "CONSENSUS_BONUS_MAXIMUM",
    "CONSENSUS_DIRECTION_CONFLICT",
    "CONSENSUS_DIRECTION_LONG",
    "CONSENSUS_DIRECTION_NEUTRAL",
    "CONSENSUS_DIRECTION_SHORT",
    "CONSENSUS_DIRECTIONS",
    "LLM_WEIGHT",
    "MARKET_ANALYSIS_ALGORITHM_VERSION",
    "MARKET_SCREENER_ALGORITHM_VERSION",
    "MARKET_SCREENER_LIMIT",
    "MARKET_SCREENER_MINIMUM_SCORE",
    "MARKET_SCREENER_SIGNAL_ALGORITHM_VERSION",
    "MINIMUM_STRATEGIES_FOR_CONSENSUS",
    "NEUTRAL_ATTENTION_SCORE",
    "NEUTRAL_LLM_SCORE",
    "QUANT_WEIGHT",
    "RANKING_INPUT_LIMIT_MULTIPLIER",
    "RANKING_INPUT_MINIMUM_LIMIT",
    "RANKING_WEIGHT_TOTAL",
    "REQUIRE_QUANT_SCORE",
    "REQUIRE_TECHNICAL_SCORE",
    "SCORE_MAXIMUM",
    "SCORE_MINIMUM",
    "SIGNAL_DIRECTION_LONG",
    "SIGNAL_DIRECTION_SHORT",
    "SIGNAL_DIRECTIONS",
    "TECHNICAL_WEIGHT",
]