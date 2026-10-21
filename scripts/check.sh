#!/usr/bin/env bash
set -euo pipefail

python -m compileall auth config http_client job_runtime jobs llm market_analysis market_data notifications prompting routers scripts services
python -m unittest discover -s tests -p 'test_*.py'
black --check .
ruff check .
