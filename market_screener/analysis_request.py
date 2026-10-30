from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ScreenerContextType = Literal[
    "WATCHLIST", "PORTFOLIO", "UNIVERSE", "ENTITY", "INSTRUMENT"
]


class ScreenerAnalysisRequest(BaseModel):
    context_type: ScreenerContextType
    context_id: UUID
