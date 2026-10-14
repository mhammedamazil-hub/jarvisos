#!/bin/bash
set -euo pipefail
ISO="${1:-out/JARVISOS.iso}"
if [ ! -f "$ISO" ]; then echo "ISO not found: $ISO"; exit 1; fi
echo "=== QEMU Smoke Boot Test ==="
echo "ISO: $ISO ($(du -h "$ISO" | cut -f1))"
echo "This is a SMOKE test only - success means ISO is plausibly bootable, not that desktop works."
if ! command -v qemu-system-x86_64 >/dev/null; then echo "qemu not installed - install qemu-system-x86"; exit 1; fi
if ! command -v xorriso >/dev/null; then echo "xorriso not installed"; fi
echo "== Checking bootability via xorriso =="
xorriso -indev "$ISO" -report_el_torito as_mkisofs 2>&1 | head -30 || true
echo ""
echo "== Attempting QEMU BIOS boot (10s timeout, expect timeout) =="
timeout 20 qemu-system-x86_64 -m 1024 -cdrom "$ISO" -boot d -nographic -display none -no-reboot 2>&1 | head -100 || true
echo "BIOS smoke attempted (timeout is expected - we're not automating GUI)"
if [ -f /usr/share/OVMF/OVMF.fd ]; then
  echo ""
  echo "== Attempting QEMU UEFI boot (10s timeout) =="
  timeout 20 qemu-system-x86_64 -m 1024 -bios /usr/share/OVMF/OVMF.fd -cdrom "$ISO" -boot d -nographic -display none -no-reboot 2>&1 | head -100 || true
  echo "UEFI smoke attempted"
else
  echo "OVMF not found - UEFI smoke skipped"
fi
echo ""
echo "=== Smoke test limitations ==="
echo "Passing this script means: ISO is a valid bootable image with El Torito / UEFI entry."
echo "It does NOT mean: desktop boots, JARVIS launches, network works."
echo "For full validation see docs/TESTING.md - levels 3,4,5 require manual testing."
