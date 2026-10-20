#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:-.}"
cd "$repo_root"

required=(
  "jobs/portfolio_intelligence/runtime.py"
  "jobs/portfolio_intelligence/messages.py"
  "jobs/portfolio_intelligence/parsing.py"
  "jobs/portfolio_intelligence/scoring.py"
  "jobs/portfolio_intelligence/branches/daily_mover.py"
  "jobs/portfolio_intelligence/branches/upcoming_catalyst.py"
  "jobs/portfolio_intelligence/branches/breaking_catalyst.py"
)

for path in "${required[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
done

python - <<'PY'
from pathlib import Path

branches = {
    "daily_mover.py": "DailyMoverBranch",
    "upcoming_catalyst.py": "UpcomingCatalystBranch",
    "breaking_catalyst.py": "BreakingCatalystBranch",
}

for filename, class_name in branches.items():
    path = Path("jobs/portfolio_intelligence/branches") / filename
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "from jobs.portfolio_intelligence.branches.base import IntelligenceBranch\n",
        "",
    )
    source = source.replace(
        f"class {class_name}(IntelligenceBranch):",
        f"class {class_name}:",
    )
    path.write_text(source, encoding="utf-8")
PY

rm -f jobs/portfolio_intelligence/context.py
rm -f jobs/portfolio_intelligence/branches/base.py
rm -rf jobs/portfolio_intelligence/messages
rm -rf jobs/portfolio_intelligence/parsing
rm -rf jobs/portfolio_intelligence/scoring

echo "Portfolio Intelligence phase 2 cleanup completed."
