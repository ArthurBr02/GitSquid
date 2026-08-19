#!/usr/bin/env bash
# Development entry point: refresh the editable install, then report status.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || ./scripts/install.sh
.venv/bin/python -m pip install --quiet -e ".[dev]"

.venv/bin/gitsquid init
.venv/bin/gitsquid index
.venv/bin/gitsquid doctor
echo "[ok] dev environment ready — run .venv/bin/gitsquid --help"
