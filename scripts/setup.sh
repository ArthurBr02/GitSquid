#!/usr/bin/env bash
# Install what building GitSquid needs on this machine, then the npm dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

missing=()
for tool in cargo node git; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "[fail] missing: ${missing[*]}" >&2
  echo "       cargo -> https://rustup.rs   node -> https://nodejs.org   git -> your package manager" >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin)
    xcode-select -p >/dev/null 2>&1 || {
      echo "[fail] Xcode command line tools are needed: xcode-select --install" >&2
      exit 1
    }
    ;;
  Linux)
    # WebKitGTK is what Tauri draws the window with; the rest is bundling.
    packages="libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev patchelf libssl-dev build-essential"
    if command -v apt-get >/dev/null; then
      echo "[info] installing: $packages"
      sudo apt-get update -qq && sudo apt-get install -y $packages
    else
      echo "[warn] not an apt system; install the equivalents of: $packages" >&2
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*)
    echo "[info] Windows needs the MSVC build tools; WebView2 ships with Windows 11."
    ;;
esac

echo "[info] installing npm dependencies"
npm --prefix desktop install --silent

echo "[ok] ready. Next:"
echo "     ./scripts/dev.sh              run the app"
echo "     ./scripts/test.sh             run every test"
echo "     ./scripts/release.sh macos    build a bundle for this platform"
