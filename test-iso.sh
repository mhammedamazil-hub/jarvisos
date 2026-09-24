#!/usr/bin/env bash
# Automated boot test: boots the ISO headless in QEMU (disposable disk, 4 GB RAM like the target
# laptop), appends jarvis.selftest to the kernel cmdline and waits for markers on the serial console.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ISO="${1:-$(ls -1t "$ROOT"/out/*.iso 2>/dev/null | head -1)}"
TIMEOUT="${JARVIS_BOOT_TIMEOUT:-600}"
[ -f "$ISO" ] || { echo "no ISO"; exit 2; }
LOG="$ROOT/out/qemu-serial.log"; mkdir -p "$ROOT/out" "$ROOT/build/vm"
WORK=$(mktemp -d); trap 'kill $QPID 2>/dev/null; rm -rf "$WORK"' EXIT
echo "[test] checking ISO structure"
xorriso -indev "$ISO" -find /live -name vmlinuz* 2>/dev/null | grep -q vmlinuz || { echo "FAIL: no kernel in ISO"; exit 1; }
xorriso -osirrox on -indev "$ISO" -extract /live "$WORK/live" >/dev/null 2>&1
K=$(ls "$WORK"/live/vmlinuz* | head -1); I=$(ls "$WORK"/live/initrd* | head -1)
qemu-img create -f qcow2 "$ROOT/build/vm/test.qcow2" 8G >/dev/null
ACCEL=(-accel tcg); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)
echo "[test] booting (timeout ${TIMEOUT}s)…"
qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 -cdrom "$ISO" -kernel "$K" -initrd "$I" \
  -append "boot=live components username=jarvis hostname=jarvisos console=ttyS0,115200 jarvis.selftest" \
  -drive file="$ROOT/build/vm/test.qcow2",if=virtio -nic user,model=virtio-net-pci \
  -display none -vga std -serial file:"$LOG" & QPID=$!
boot=FAIL; self=FAIL; t=0
while ((t < TIMEOUT)); do
  grep -q JARVISOS-BOOT-OK "$LOG" 2>/dev/null && boot=PASS
  grep -q JARVIS-SELFTEST-PASS "$LOG" 2>/dev/null && { self=PASS; break; }
  grep -q JARVIS-SELFTEST-FAIL "$LOG" 2>/dev/null && break
  grep -qi "kernel panic" "$LOG" 2>/dev/null && break
  sleep 5; t=$((t+5))
done
echo "QEMU boot test:     $boot"
echo "JARVIS self-test:   $self   (desktop session + core + network)"
echo "serial log:         $LOG"
[ "$boot" = PASS ] && [ "$self" = PASS ]
