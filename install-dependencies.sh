#!/usr/bin/env bash
# Installs host build/test dependencies (Debian/Ubuntu). Touches nothing but packages.
set -euo pipefail
PKGS=(live-build debootstrap xorriso squashfs-tools mtools dosfstools grub-pc-bin grub-efi-amd64-bin
      syslinux isolinux syslinux-common qemu-system-x86 qemu-utils ovmf python3 python3-pytest python3-psutil
      python3-numpy python3-pyqt5 rsync)
echo "[deps] installing: ${PKGS[*]}"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PKGS[@]}"
echo "[deps] done"
