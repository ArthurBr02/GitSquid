#!/usr/bin/env bash
# Run the full test suite.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || ./scripts/install.sh
exec .venv/bin/python -m pytest "$@"
