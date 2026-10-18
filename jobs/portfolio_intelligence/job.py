from jobs.portfolio_intelligence.branches.daily_mover import DailyMoverBranch
from jobs.portfolio_intelligence.branches.upcoming_catalyst import UpcomingCatalystBranch
from jobs.portfolio_intelligence.branches.breaking_catalyst import BreakingCatalystBranch
from jobs.portfolio_intelligence.runtime import run_branch

def run():
    results={}
    for branch in (DailyMoverBranch(),UpcomingCatalystBranch(),BreakingCatalystBranch()):
        results[branch.name]=run_branch(branch)
    statuses={r["status"] for r in results.values()}
    status="SUCCESS" if statuses=={"SUCCESS"} else "FAILED" if statuses=={"FAILED"} else "PARTIAL"
    return {"status":status,"branches":results}
if __name__=="__main__": print(run())
