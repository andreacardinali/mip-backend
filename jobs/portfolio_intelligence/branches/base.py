from abc import ABC, abstractmethod

from jobs.portfolio_intelligence.context import JobContext
from jobs.portfolio_intelligence.models import BranchResult


class IntelligenceBranch(ABC):
    name: str

    @abstractmethod
    def execute(self, context: JobContext) -> BranchResult:
        raise NotImplementedError
