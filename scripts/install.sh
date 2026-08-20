#!/usr/bin/env bash
# Create the virtualenv and install gitsquid with its development dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"gitsquid needs Python 3.12+, found {sys.version.split()[0]}")
PY

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dev]"

echo "[ok] installed. Next:"
echo "     source .venv/bin/activate"
echo "     gitsquid ui"
