from job_runtime.models import JobStatus
from jobs.portfolio_intelligence.branches.breaking_catalyst import BreakingCatalystBranch
from jobs.portfolio_intelligence.branches.daily_mover import DailyMoverBranch
from jobs.portfolio_intelligence.branches.upcoming_catalyst import UpcomingCatalystBranch
from jobs.portfolio_intelligence.runtime import run_branch


BRANCHES = (
    DailyMoverBranch(),
    UpcomingCatalystBranch(),
    BreakingCatalystBranch(),
)


def run() -> dict:
    results = {branch.name: run_branch(branch) for branch in BRANCHES}
    statuses = {result["status"] for result in results.values()}
    if statuses == {JobStatus.SUCCESS.value}:
        status = JobStatus.SUCCESS
    elif statuses == {JobStatus.FAILED.value}:
        status = JobStatus.FAILED
    else:
        status = JobStatus.PARTIAL
    return {
        "status": status.value,
        "branches": results,
    }


if __name__ == "__main__":
    print(run())
