#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"
python3 apply_patch.py
python3 -m compileall -q market_sources market_data market_analysis services jobs
pytest -q
