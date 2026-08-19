#!/usr/bin/env bash
# Build the wheel and sdist into dist/.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || ./scripts/install.sh
.venv/bin/python -m pip install --quiet build
rm -rf dist build ./*.egg-info
.venv/bin/python -m build

echo "[ok] artefacts:"
ls -1 dist
