#!/usr/bin/env bash
# Production-style local run: build a wheel, install it into a clean venv, run gitsquid from it.
# Usage: ./scripts/run.sh [gitsquid arguments...]   (default: ui)
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build.sh >/dev/null
WHEEL="$(ls -t dist/*.whl | head -1)"

PYTHON="${PYTHON:-python3}"
[ -d .venv-prod ] || "$PYTHON" -m venv .venv-prod
.venv-prod/bin/python -m pip install --quiet --upgrade pip
.venv-prod/bin/python -m pip install --quiet --force-reinstall "$WHEEL"

echo "[ok] running $WHEEL"
exec .venv-prod/bin/gitsquid "${@:-ui}"
