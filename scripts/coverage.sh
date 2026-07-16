#!/usr/bin/env bash
# Optional coverage report for hot-path modules. Not part of verify.py.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m pip install -q 'pytest-cov>=5,<7'

python -m pytest tests/ \
  --cov=nokori/search \
  --cov=nokori/matcher \
  --cov=nokori/lifecycle \
  --cov=nokori/gate \
  --cov-report=term-missing:skip-covered \
  -q \
  "$@"
