#!/usr/bin/env bash
set -euo pipefail
repo_root="${1:-.}"
cd "$repo_root"

python - <<'PY'
from pathlib import Path

for filename, label in (
    ("upcoming_catalyst.py", "Upcoming"),
    ("breaking_catalyst.py", "Breaking"),
):
    path = Path("jobs/portfolio_intelligence/branches") / filename
    source = path.read_text(encoding="utf-8")

    collection_import = "from job_runtime.collections import batched, deduplicate_by\n"
    batching_import = (
        "from jobs.portfolio_intelligence.batching import matched_batch_rows\n"
    )
    if batching_import not in source:
        source = source.replace(
            collection_import,
            collection_import + batching_import,
            1,
        )

    old = f'''            rows = response.data.get("results") or []
            if len(rows) != len(batch):
                raise ValueError("{label} LLM result count mismatch")

            for source, row in zip(batch, rows, strict=True):
                if row.get("symbol") != source.symbol:
                    raise ValueError("{label} LLM symbol mismatch")
'''
    new = f'''            for source, row in matched_batch_rows(
                batch,
                response.data,
                label="{label}",
            ):
'''
    if old in source:
        source = source.replace(old, new, 1)
    elif new not in source:
        raise SystemExit(f"Expected batch matching block not found in {path}")

    path.write_text(source, encoding="utf-8")
PY

rm -f jobs/portfolio_intelligence/job.py

echo "Portfolio Intelligence phase 3 applied."
