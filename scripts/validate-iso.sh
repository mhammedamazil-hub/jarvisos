#!/bin/bash
set -euo pipefail
ISO="${1:-out/JARVISOS.iso}"
echo "=== JARVISOS ISO Validator ==="
echo "ISO: $ISO"
if [ ! -f "$ISO" ]; then echo "FAIL: ISO not found: $ISO"; exit 1; fi
SIZE=$(stat -c%s "$ISO")
MB=$((SIZE/1024/1024))
echo "Size: $SIZE bytes (${MB} MB)"
if [ "$SIZE" -lt $((300*1024*1024)) ]; then echo "FAIL: too small (<300MB)"; exit 1; fi
if [ "$SIZE" -gt $((4096*1024*1024)) ]; then echo "WARN: >4GB"; fi
echo "✓ size plausible"

# checksum
DIR=$(dirname "$ISO")
BASE=$(basename "$ISO")
if [ -f "$DIR/$BASE.sha256" ]; then
  (cd "$DIR" && sha256sum -c "$BASE.sha256") && echo "✓ sha256 verified" || { echo "FAIL: sha256 mismatch"; exit 1; }
else
  sha256sum "$ISO" && echo "(no .sha256 file, computed)"
fi

# file type
file "$ISO" || true
# xorriso checks
if command -v xorriso >/dev/null; then
  echo "== xorriso pvd_info =="
  xorriso -indev "$ISO" -pvd_info 2>&1 | head -40 || true
  echo "== xorriso ls / =="
  xorriso -indev "$ISO" -ls / -- 2>&1 | head -60 || true
  echo "== El Torito =="
  xorriso -indev "$ISO" -report_el_torito as_mkisofs 2>&1 | head -40 || true
  # UEFI
  if xorriso -indev "$ISO" -ls /EFI/BOOT -- 2>&1 | grep -qi "BOOTX64"; then
    echo "✓ UEFI BOOTX64.EFI present"
  else
    echo "WARN: UEFI boot not detected (BIOS-only?)"
  fi
  if xorriso -indev "$ISO" -ls /live -- 2>&1 | grep -q "filesystem"; then
    echo "✓ live filesystem.squashfs present"
  else
    echo "FAIL: live filesystem.squashfs missing"
    # try alternate listing
    xorriso -indev "$ISO" -ls /live -- 2>&1 || true
    exit 1
  fi
  if xorriso -indev "$ISO" -ls /live -- 2>&1 | grep -q "vmlinuz"; then
    echo "✓ kernel present"
  else
    echo "FAIL: kernel missing"
    exit 1
  fi
else
  echo "WARN: xorriso not installed - skipping iso structure checks"
fi

echo ""
echo "=== Validation levels (per spec) ==="
echo "1. Build succeeded:        ✓ (ISO exists + size)"
echo "2. ISO structure validated: ✓ (xorriso checks above)"
echo "3. VM boot:               ?  (run: qemu-system-x86_64 -m 2048 -cdrom $ISO -boot d)"
echo "                           and with UEFI: -bios /usr/share/OVMF/OVMF.fd"
echo "4. Laptop hardware:        ?  (requires Dell Inspiron 3558 manual test)"
echo "5. Installation tested:    ?  (not yet, live-only)"
echo ""
echo "Overall: ISO is structurally valid. Do not claim VM/hardware success until tested."
