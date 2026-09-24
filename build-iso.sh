#!/usr/bin/env bash
# Reproducible JARVISOS ISO build (Debian live-build). Never touches host disks/bootloader.
#   ./build-iso.sh                 # standard ISO
#   JARVIS_WITH_MEDIAPIPE=1 ./build-iso.sh   # include MediaPipe (hand tracking) ~+120 MB
#   JARVIS_WITH_VOSK=1 ./build-iso.sh        # include offline speech model ~+40 MB
#   JARVIS_INSTALLER=live ./build-iso.sh     # add Debian installer (test in QEMU only!)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="${JARVIS_BUILD_DIR:-$ROOT/build/iso}"
OUT="$ROOT/out"
START=$(date +%s)
red(){ printf '\033[91m%s\033[0m\n' "$*"; }; grn(){ printf '\033[92m%s\033[0m\n' "$*"; }
missing=()
for c in lb debootstrap xorriso mksquashfs; do command -v "$c" >/dev/null || missing+=("$c"); done
if ((${#missing[@]})); then red "Missing dependencies: ${missing[*]} — run ./install-dependencies.sh"; exit 2; fi
[ "$(id -u)" = 0 ] || SUDO=sudo
echo "[1/5] running unit tests"
python3 -m pytest -q "$ROOT/tests" || { red "tests failed — refusing to build"; exit 3; }
echo "[2/5] preparing live-build tree in $WORK"
${SUDO:-} rm -rf "$WORK"; mkdir -p "$WORK" "$OUT"
cp -a "$ROOT/iso/auto" "$ROOT/iso/config" "$WORK/"
DEST="$WORK/config/includes.chroot/opt/jarvisos"
mkdir -p "$DEST"
rsync -a --exclude '__pycache__' "$ROOT/jarvis" "$ROOT/requirements-iso.txt" "$ROOT/config" "$DEST/"
[ "${JARVIS_WITH_MEDIAPIPE:-0}" = 1 ] && touch "$DEST/.with-mediapipe"
[ "${JARVIS_WITH_VOSK:-0}" = 1 ] && touch "$DEST/.with-vosk-model"
git -C "$ROOT" rev-parse --short HEAD > "$DEST/VERSION" 2>/dev/null || echo dev > "$DEST/VERSION"
export SOURCE_DATE_EPOCH="$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || date +%s)"
echo "[3/5] lb config"
cd "$WORK"
${SUDO:-} env JARVIS_INSTALLER="${JARVIS_INSTALLER:-none}" JARVIS_MIRROR="${JARVIS_MIRROR:-}" lb config
echo "[4/5] lb build (this takes 20–60 min)"
if ! ${SUDO:-} env SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" lb build 2>&1 | tee "$OUT/build.log"; then
  red "BUILD FAILED — see $OUT/build.log"; tail -30 "$OUT/build.log"; exit 1; fi
ISO=$(ls -1 "$WORK"/*.iso 2>/dev/null | head -1)
[ -n "$ISO" ] || { red "BUILD FAILED — no ISO produced (see $OUT/build.log)"; exit 1; }
NAME="jarvisos-$(cat "$DEST/VERSION")-amd64.iso"
${SUDO:-} mv "$ISO" "$OUT/$NAME"; ${SUDO:-} chown "$(id -u):$(id -g)" "$OUT/$NAME"
echo "[5/5] checksums"
( cd "$OUT" && sha256sum "$NAME" > "$NAME.sha256" )
grn "ISO:       $OUT/$NAME"
grn "Size:      $(du -h "$OUT/$NAME" | cut -f1)"
grn "SHA256:    $(cut -d' ' -f1 "$OUT/$NAME.sha256")"
grn "Duration:  $(( ($(date +%s)-START)/60 )) min $(( ($(date +%s)-START)%60 )) s"
echo "Next: ./test-iso.sh $OUT/$NAME"
