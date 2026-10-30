from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PipelinePhase = Literal[
    "discovery_refresh",
    "candidate_resolver",
    "analysis_scan",
    "llm_enrichment",
    "ranking",
]


class MarketScreenerPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phases: list[PipelinePhase] | None = Field(default=None, min_length=1)
    stop_on_error: bool | None = None
