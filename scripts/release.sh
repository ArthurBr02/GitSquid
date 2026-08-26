#!/usr/bin/env bash
# Build an installable bundle for one platform, and collect it in dist/.
# Tauri bundles for the operating system it runs on: there is no cross-compilation.
set -euo pipefail
cd "$(dirname "$0")/.."

target="${1:-}"
case "$target" in
  macos|linux|windows) ;;
  *)
    echo "usage: $0 <macos|linux|windows> [--universal]" >&2
    exit 2
    ;;
esac
shift

npm --prefix desktop run "build:$target" -- "$@"

mkdir -p dist
found=$(find target/release/bundle -type f \
  \( -name '*.dmg' -o -name '*.app.tar.gz' -o -name '*.deb' -o -name '*.AppImage' \
     -o -name '*.msi' -o -name '*.exe' \) 2>/dev/null || true)
if [ -z "$found" ]; then
  echo "[fail] no bundle was produced." >&2
  exit 1
fi
echo "$found" | while read -r artefact; do cp "$artefact" dist/; done

echo "[ok] in dist/:"
ls -1 dist
