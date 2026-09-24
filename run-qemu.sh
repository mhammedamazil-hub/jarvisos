#!/usr/bin/env bash
# Boot the ISO in QEMU with a DISPOSABLE virtual disk. Never uses host disks.
#   ./run-qemu.sh [iso] [--uefi] [--install] [--ram 4096] [--headless]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ISO="${1:-$(ls -1t "$ROOT"/out/*.iso 2>/dev/null | head -1)}"; shift || true
[ -f "$ISO" ] || { echo "no ISO found; run ./build-iso.sh"; exit 2; }
RAM=4096; UEFI=0; INSTALL=0; HEADLESS=0; EXTRA=()
while (($#)); do case "$1" in --uefi) UEFI=1;; --install) INSTALL=1;; --headless) HEADLESS=1;;
  --ram) RAM="$2"; shift;; *) EXTRA+=("$1");; esac; shift; done
mkdir -p "$ROOT/build/vm"
DISK="$ROOT/build/vm/disposable.qcow2"
if ((INSTALL)); then rm -f "$DISK"; qemu-img create -f qcow2 "$DISK" 20G >/dev/null; echo "fresh disposable disk: $DISK"; fi
[ -f "$DISK" ] || qemu-img create -f qcow2 "$DISK" 20G >/dev/null
ACCEL=(-accel tcg); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)
FW=(); if ((UEFI)); then cp /usr/share/OVMF/OVMF_VARS.fd "$ROOT/build/vm/vars.fd"
  FW=(-drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd -drive if=pflash,format=raw,file="$ROOT/build/vm/vars.fd"); fi
DISP=(-display gtk -vga virtio); ((HEADLESS)) && DISP=(-display none -vga std)
exec qemu-system-x86_64 "${ACCEL[@]}" -m "$RAM" -smp 2 "${FW[@]}" \
  -cdrom "$ISO" -boot d -drive file="$DISK",if=virtio,format=qcow2 \
  -nic user,model=virtio-net-pci -audiodev none,id=a0 -device intel-hda -device hda-duplex,audiodev=a0 \
  -usb -device usb-tablet "${DISP[@]}" -serial mon:stdio "${EXTRA[@]}"
