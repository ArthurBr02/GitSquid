#!/usr/bin/env bash
# Run GitSquid from source, rebuilding on change.
set -euo pipefail
cd "$(dirname "$0")/.."
exec npm --prefix desktop run dev
