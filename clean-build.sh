#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="${JARVIS_BUILD_DIR:-$ROOT/build/iso}"
[ -d "$WORK" ] && (cd "$WORK" && sudo lb clean --purge || true)
sudo rm -rf "$WORK"
[ "${1:-}" = "--all" ] && rm -rf "$ROOT/out" "$ROOT/build/vm"
echo "clean."
