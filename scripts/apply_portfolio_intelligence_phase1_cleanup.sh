#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:-.}"
cd "$repo_root"

required=(
  "job_runtime/collections.py"
  "notifications/diagnostics.py"
  "jobs/portfolio_intelligence/branches/daily_mover.py"
  "jobs/portfolio_intelligence/branches/upcoming_catalyst.py"
  "jobs/portfolio_intelligence/branches/breaking_catalyst.py"
)

for path in "${required[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required replacement file: $path" >&2
    exit 1
  fi
done

rm -f jobs/portfolio_intelligence/notifications.py
rm -f jobs/portfolio_intelligence/utils/collections.py
rm -f jobs/portfolio_intelligence/utils/__init__.py
rm -rf jobs/portfolio_intelligence/utils/__pycache__
rmdir jobs/portfolio_intelligence/utils 2>/dev/null || true

echo "Portfolio Intelligence phase 1 cleanup completed."
