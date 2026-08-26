#!/usr/bin/env bash
# The whole suite: the Rust engine, then the page's own JavaScript.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[info] engine"
cargo test --workspace "$@"

echo "[info] page"
npm --prefix desktop test

echo "[ok] everything passed."
