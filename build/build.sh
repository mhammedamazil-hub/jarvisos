#!/bin/bash
set -euo pipefail

# JARVISOS build - reproducible Debian bookworm live ISO
# Usage: sudo ./build/build.sh [--clean] [--dry-run] [--fast]
# Designed for GitHub Actions and local Debian 12 host with ~10GB free.
# Produces: out/JARVISOS.iso + out/JARVISOS.iso.sha256

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build/work"
CHROOT="${BUILD_DIR}/chroot"
ISO_DIR="${BUILD_DIR}/iso"
OUT_DIR="${REPO_ROOT}/out"
CACHE_DIR="${BUILD_DIR}/apt-cache"
OVERLAY="${REPO_ROOT}/overlay"
CONFIG_DIR="${REPO_ROOT}/config"
DEBIAN_SUITE="bookworm"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
DEBIAN_SECURITY="${DEBIAN_SECURITY:-http://security.debian.org/debian-security}"
ARCH="amd64"
ISO_LABEL="JARVISOS"
ISO_APPID="JARVISOS Live"
ISO_PUBLISHER="JARVISOS Project"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"

DRY_RUN=0
FAST=0
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --fast) FAST=1 ;;
    --clean) CLEAN=1 ;;
  esac
done

log() { echo -e "\033[1;34m[$(date +%H:%M:%S)] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m" >&2; }
err() { echo -e "\033[1;31m[ERR] $*\033[0m" >&2; }

# ensure output dirs
mkdir -p "${OUT_DIR}" "${BUILD_DIR}"

if [ "$CLEAN" = "1" ]; then
  log "Cleaning ${BUILD_DIR} ..."
  # unmount first if needed
  for m in proc sys dev/pts dev run; do
    if mountpoint -q "${CHROOT}/${m}" 2>/dev/null; then
      umount -l "${CHROOT}/${m}" || true
    fi
  done
  rm -rf "${BUILD_DIR}"
  mkdir -p "${OUT_DIR}" "${BUILD_DIR}"
  log "Clean done"
  exit 0
fi

# trap cleanup
cleanup() {
  local rc=$?
  log "Cleaning up mounts..."
  for m in proc sys dev/pts dev run; do
    if mountpoint -q "${CHROOT}/${m}" 2>/dev/null; then
      umount -l "${CHROOT}/${m}" || true
    fi
  done
  if [ $rc -ne 0 ]; then
    err "Build failed with exit $rc - check logs above"
    err "Partial build retained in ${BUILD_DIR} for inspection"
  fi
}
trap cleanup EXIT

if [ "$DRY_RUN" = "1" ]; then
  log "=== DRY RUN MODE - validating configuration only ==="
fi

# checks
check_host_deps() {
  log "Checking host dependencies..."
  local deps=(debootstrap mksquashfs xorriso grub-mkstandalone mcopy mkfs.vfat)
  local missing=()
  for d in "${deps[@]}"; do
    if ! command -v "$d" >/dev/null 2>&1; then
      # map binary to package
      case "$d" in
        debootstrap) missing+=("debootstrap") ;;
        mksquashfs) missing+=("squashfs-tools") ;;
        xorriso) missing+=("xorriso") ;;
        grub-mkstandalone) missing+=("grub-efi-amd64-bin grub-pc-bin") ;;
        mcopy) missing+=("mtools") ;;
        mkfs.vfat) missing+=("dosfstools") ;;
      esac
    fi
  done
  # also need grub-efi-amd64-bin for standalone
  if [ ${#missing[@]} -gt 0 ]; then
    warn "Missing host packages: ${missing[*]}"
    if [ "$DRY_RUN" = "1" ]; then
      log "Dry run: would install ${missing[*]}"
    else
      if [ "$(id -u)" != "0" ]; then
        err "Please run as root to install dependencies or install manually: apt-get install ${missing[*]}"
        return 1
      fi
      log "Installing missing deps: ${missing[*]}"
      apt-get update
      apt-get install -y --no-install-recommends ${missing[*]} isolinux syslinux-common 2>&1 | tail -20 || true
    fi
  else
    log "All host deps present"
  fi
  # print versions
  debootstrap --version 2>&1 | head -1 || true
  mksquashfs -version 2>&1 | head -1 || true
  xorriso --version 2>&1 | head -1 || true
  grub-mkstandalone --version 2>&1 | head -1 || true
}

validate_config() {
  log "Validating package lists and overlay..."
  local count=0
  for f in "${CONFIG_DIR}/package-lists/"*.list.chroot; do
    [ -f "$f" ] || continue
    # count non-comment, non-empty lines
    local n=$(grep -v "^\s*#" "$f" | grep -v "^\s*$" | wc -l)
    log "  $(basename $f): $n packages"
    count=$((count + n))
    # check for forbidden (check for embedded keys pattern)
    if grep -qi "api_key.*sk-" "$f" 2>/dev/null; then
      err "Potential API key in $f - aborting"
      exit 1
    fi
  done
  log "Total packages listed: $count"
  if [ ! -d "$OVERLAY" ]; then
    err "Overlay dir missing: $OVERLAY"
    exit 1
  fi
  # validate jarvis-center syntax
  if command -v python3 >/dev/null; then
    python3 -m py_compile "${OVERLAY}/usr/local/bin/jarvis-center" && log "jarvis-center syntax OK" || { err "jarvis-center syntax fail"; exit 1; }
    # also check for embedded secrets
    if grep -q "sk-[A-Za-z0-9]\{20\}" "${OVERLAY}/usr/local/bin/jarvis-center" 2>/dev/null; then
      err "Potential secret in jarvis-center - abort"
      exit 1
    fi
  fi
  # check overlay perms
  find "${OVERLAY}" -type f -name "*.sh" -o -name "jarvis*" | head -10 || true
  log "Config validation passed"
}

if [ "$DRY_RUN" = "1" ]; then
  check_host_deps
  validate_config
  log "Dry run: would proceed to debootstrap + chroot build (requires root, ~6GB free)"
  log "To build for real: sudo ./build/build.sh"
  exit 0
fi

# require root for real build
if [ "$(id -u)" != "0" ]; then
  err "This script must be run as root (sudo ./build/build.sh)"
  exit 1
fi

check_host_deps
validate_config

# free space check
avail_kb=$(df --output=avail "$REPO_ROOT" | tail -1 | tr -d ' ')
avail_gb=$((avail_kb / 1024 / 1024))
log "Free space: ${avail_gb}GB (need ~10GB)"
if [ "$avail_gb" -lt 8 ]; then
  warn "Low disk space - build may fail"
fi

# debootstrap
if [ ! -f "${CHROOT}/etc/debian_version" ]; then
  log "=== Stage 1: debootstrap ${DEBIAN_SUITE} (${ARCH}) ==="
  mkdir -p "${CHROOT}" "${CACHE_DIR}"
  # use cache if available
  if [ -d "${CACHE_DIR}/lists" ]; then
    log "Using apt cache at ${CACHE_DIR}"
  fi
  debootstrap --arch="${ARCH}" --variant=minbase "${DEBIAN_SUITE}" "${CHROOT}" "${DEBIAN_MIRROR}" || {
    err "debootstrap failed - check mirror ${DEBIAN_MIRROR}"
    exit 1
  }
  log "debootstrap done - $(cat ${CHROOT}/etc/debian_version) $(cat ${CHROOT}/etc/os-release | grep PRETTY)"
else
  log "Chroot exists - skipping debootstrap (use --clean to rebuild)"
fi

# mounts
mount_chroot() {
  log "Mounting chroot pseudo-filesystems"
  mount -t proc /proc "${CHROOT}/proc" 2>/dev/null || true
  mount -t sysfs /sys "${CHROOT}/sys" 2>/dev/null || true
  mount --bind /dev "${CHROOT}/dev" 2>/dev/null || true
  mount --bind /dev/pts "${CHROOT}/dev/pts" 2>/dev/null || true
  mkdir -p "${CHROOT}/run"
  mount -t tmpfs tmpfs "${CHROOT}/run" 2>/dev/null || true
  # apt cache bind
  mkdir -p "${CHROOT}/var/cache/apt/archives" "${CACHE_DIR}"
  mount --bind "${CACHE_DIR}" "${CHROOT}/var/cache/apt/archives" 2>/dev/null || true
}
mount_chroot

# chroot helper
chroot_exec() {
  chroot "${CHROOT}" /bin/bash -c "$*"
}

# configure apt
log "=== Stage 2: Configure APT inside chroot ==="
cat > "${CHROOT}/etc/apt/sources.list" <<EOF
deb ${DEBIAN_MIRROR} ${DEBIAN_SUITE} main contrib non-free non-free-firmware
deb ${DEBIAN_MIRROR} ${DEBIAN_SUITE}-updates main contrib non-free non-free-firmware
deb ${DEBIAN_SECURITY} ${DEBIAN_SUITE}-security main contrib non-free non-free-firmware
deb ${DEBIAN_MIRROR} ${DEBIAN_SUITE}-backports main contrib non-free non-free-firmware
EOF
cat "${CHROOT}/etc/apt/sources.list"
# apt options for reproducibility
export DEBIAN_FRONTEND=noninteractive
chroot_exec "apt-get update" || { err "apt update failed"; exit 1; }
chroot_exec "apt-get install -y --no-install-recommends apt-utils ca-certificates gnupg" || true

# install packages
log "=== Stage 3: Install packages (this takes 10-20 min) ==="
# collect packages
PKG_LIST=$(grep -h -v "^\s*#" ${CONFIG_DIR}/package-lists/*.list.chroot | grep -v "^\s*$" | tr '\n' ' ')
log "Installing $(echo $PKG_LIST | wc -w) packages..."
# log package list for debugging
echo "$PKG_LIST" | tr ' ' '\n' | sort > "${BUILD_DIR}/installed-packages.txt"
# install in chroot
chroot_exec "apt-get install -y --no-install-recommends $PKG_LIST" || {
  warn "Some packages failed - trying without firmware first"
  # retry without firmware
  PKG_NOFW=$(echo "$PKG_LIST" | tr ' ' '\n' | grep -v firmware | tr '\n' ' ')
  chroot_exec "apt-get install -y --no-install-recommends $PKG_NOFW" || { err "Package install failed"; exit 1; }
}
# ensure kernel exists
if ! ls "${CHROOT}/boot/vmlinuz"* >/dev/null 2>&1; then
  err "No kernel found in /boot after install"
  ls -lh "${CHROOT}/boot/" || true
  exit 1
fi
ls -lh "${CHROOT}/boot/" 2>&1 | head -20

# overlay copy
log "=== Stage 4: Apply JARVIS overlay ==="
# rsync if available else cp -a
if command -v rsync >/dev/null; then
  rsync -a "${OVERLAY}/" "${CHROOT}/"
else
  cp -a "${OVERLAY}/." "${CHROOT}/"
fi
# ensure perms
chmod +x "${CHROOT}/usr/local/bin/jarvis"* 2>/dev/null || true
chmod +x "${CHROOT}/etc/skel/.config/openbox/autostart" 2>/dev/null || true
# run hooks
for hook in "${CONFIG_DIR}/hooks/live/"*.chroot; do
  [ -x "$hook" ] || continue
  log "Running hook $(basename $hook) ..."
  cp "$hook" "${CHROOT}/tmp/hook.sh"
  chroot_exec "bash /tmp/hook.sh" || warn "Hook $(basename $hook) failed"
  rm -f "${CHROOT}/tmp/hook.sh"
done

# configure system inside chroot
log "=== Stage 5: System configuration ==="
chroot_exec "update-desktop-database /usr/share/applications || true"
chroot_exec "gtk-update-icon-cache -f /usr/share/icons/hicolor || true" 2>&1 | tail -5 || true
# set default target graphical
chroot_exec "systemctl set-default graphical.target || true"
# enable live user already done in hook, but ensure
chroot_exec "id jarvis || useradd -m -s /bin/bash -G sudo,audio,video,netdev,bluetooth,plugdev jarvis && echo 'jarvis:jarvis' | chpasswd; echo 'jarvis ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/jarvis-live; chmod 440 /etc/sudoers.d/jarvis-live; echo jarvisos > /etc/hostname; echo '127.0.1.1 jarvisos' >> /etc/hosts" || true
# clean up apt
chroot_exec "apt-get clean" || true
# no machine-id
chroot_exec "truncate -s 0 /etc/machine-id || true; rm -f /var/lib/dbus/machine-id || true"

# unmount before squashfs
log "Unmounting chroot..."
for m in proc sys dev/pts dev run var/cache/apt/archives; do
  if mountpoint -q "${CHROOT}/${m}" 2>/dev/null; then
    umount -l "${CHROOT}/${m}" || true
  fi
done

# squashfs
log "=== Stage 6: Create squashfs (mksquashfs) ==="
mkdir -p "${ISO_DIR}/live"
SQUASH="${ISO_DIR}/live/filesystem.squashfs"
# exclude some dirs to save space but keep needed
if [ "$FAST" = "1" ]; then
  log "FAST mode: lower compression"
  mksquashfs "${CHROOT}" "${SQUASH}" -comp gzip -Xcompression-level 1 -noappend -wildcards -e "var/cache/apt/*" "var/log/*" "tmp/*" "proc/*" "sys/*" "dev/*" || { err "mksquashfs failed"; exit 1; }
else
  # use xz for better ratio but slower; gzip is faster for CI, choose xz with moderate
  mksquashfs "${CHROOT}" "${SQUASH}" -comp xz -Xdict-size 100% -noappend -wildcards -e "var/cache/apt/*" "var/log/*" "tmp/*" "proc/*" "sys/*" "dev/*" || { err "mksquashfs failed"; exit 1; }
fi
ls -lh "${SQUASH}"
du -sh "${SQUASH}"

# prepare ISO tree
log "=== Stage 7: Prepare ISO tree ==="
mkdir -p "${ISO_DIR}/boot/grub" "${ISO_DIR}/EFI/BOOT" "${ISO_DIR}/isolinux"

# copy kernel and initrd (handle multiple kernels, pick newest)
KVER=$(ls "${CHROOT}/boot/vmlinuz"* | sort -V | tail -1)
IVER=$(ls "${CHROOT}/boot/initrd.img"* | sort -V | tail -1)
if [ -z "$KVER" ] || [ -z "$IVER" ]; then
  err "Kernel/initrd missing"
  ls -lh "${CHROOT}/boot/"
  exit 1
fi
log "Kernel: $(basename $KVER) Initrd: $(basename $IVER)"
cp "$KVER" "${ISO_DIR}/live/vmlinuz"
cp "$IVER" "${ISO_DIR}/live/initrd.img"

# grub configs
cat > "${ISO_DIR}/boot/grub/grub.cfg" <<'GRUBCFG'
set timeout=7
set default=0
insmod all_video
insmod gfxterm
insmod iso9660
insmod part_msdos
insmod part_gpt
insmod fat
insmod search_fs_uuid
terminal_output gfxterm
set menu_color_normal=white/black
set menu_color_highlight=black/cyan
search --no-floppy --set=root -l JARVISOS
menuentry "JARVISOS Live" {
    linux /live/vmlinuz boot=live quiet splash components username=jarvis hostname=jarvisos noeject fetch=http://  net.ifnames=0
    initrd /live/initrd.img
}
menuentry "JARVISOS Live (verbose)" {
    linux /live/vmlinuz boot=live components username=jarvis hostname=jarvisos noeject
    initrd /live/initrd.img
}
menuentry "JARVISOS Live (failsafe)" {
    linux /live/vmlinuz boot=live components username=jarvis hostname=jarvisos noeject noapic noapm nodma nomce nolapic nomodeset nosmp vga=normal
    initrd /live/initrd.img
}
GRUBCFG

# create embedded grub config for EFI standalone
mkdir -p "${BUILD_DIR}/grub-efi"
cat > "${BUILD_DIR}/grub-efi/grub.cfg" <<'EFICFG'
search --no-floppy --set=root -l JARVISOS
set prefix=($root)/boot/grub
configfile $prefix/grub.cfg
EFICFG

log "Building GRUB EFI standalone..."
# need grub-efi target
if command -v grub-mkstandalone >/dev/null; then
  grub-mkstandalone -O x86_64-efi -o "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" "boot/grub/grub.cfg=${BUILD_DIR}/grub-efi/grub.cfg" || {
    warn "grub-mkstandalone failed, trying grub-mkimage"
    grub-mkimage -O x86_64-efi -o "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" -p /boot/grub part_gpt part_msdos iso9660 fat normal search search_fs_uuid linux linux16 echo configfile || warn "grub EFI build failed - ISO will be BIOS-only"
  }
else
  warn "grub-mkstandalone not found - EFI boot will be limited"
fi

# create FAT efi image for El Torito UEFI boot
EFI_IMG="${ISO_DIR}/boot/grub/efi.img"
if [ -f "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" ]; then
  log "Creating EFI FAT image..."
  dd if=/dev/zero of="${EFI_IMG}" bs=1M count=12 status=none
  mkfs.vfat -n JARVIS_EFI "${EFI_IMG}" >/dev/null
  # use mtools to copy
  mmd -i "${EFI_IMG}" ::/EFI 2>/dev/null || true
  mmd -i "${EFI_IMG}" ::/EFI/BOOT 2>/dev/null || true
  mcopy -i "${EFI_IMG}" "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI || warn "mcopy EFI failed"
  # also copy grub.cfg
  mcopy -i "${EFI_IMG}" "${ISO_DIR}/boot/grub/grub.cfg" ::/EFI/BOOT/grub.cfg 2>/dev/null || true
  ls -lh "${EFI_IMG}" "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI"
else
  warn "No BOOTX64.EFI - skipping efi.img, ISO will be BIOS-only"
  # create empty 1M to satisfy xorriso if needed
  dd if=/dev/zero of="${EFI_IMG}" bs=1M count=1 status=none || true
fi

# also need MBR template for hybrid
GRUB_MBR=""
if [ -f "/usr/lib/grub/i386-pc/boot_hybrid.img" ]; then
  GRUB_MBR="/usr/lib/grub/i386-pc/boot_hybrid.img"
elif [ -f "/usr/lib/grub/i386-pc/isohdpfx.bin" ]; then
  GRUB_MBR="/usr/lib/grub/i386-pc/isohdpfx.bin"
fi
if [ -n "$GRUB_MBR" ]; then
  log "Using GRUB MBR: $GRUB_MBR"
else
  warn "GRUB MBR not found - BIOS boot may be limited"
fi

# create ISO via xorriso
log "=== Stage 8: Build ISO with xorriso ==="
ISO_OUT="${OUT_DIR}/JARVISOS.iso"
mkdir -p "${OUT_DIR}"
# remove old
rm -f "${ISO_OUT}"

# Determine xorriso args for hybrid UEFI/BIOS
XORRISO_ARGS=()
XORRISO_ARGS+=(-as mkisofs)
XORRISO_ARGS+=(-r -V "${ISO_LABEL}" -A "${ISO_APPID}" -publisher "${ISO_PUBLISHER}")
XORRISO_ARGS+=(-J -joliet-long)
XORRISO_ARGS+=(-isohybrid-mbr "${GRUB_MBR}")
XORRISO_ARGS+=(-partition_offset 16)
XORRISO_ARGS+=(-b boot/grub/i386-pc/eltorito.img)
XORRISO_ARGS+=(-c boot.catalog)
XORRISO_ARGS+=(-no-emul-boot -boot-load-size 4 -boot-info-table)
XORRISO_ARGS+=(--grub2-boot-info)
# UEFI
# Pre-build eltorito.img for BIOS: need grub's el-torito image - create if missing
BIOS_ELTORITO="${ISO_DIR}/boot/grub/i386-pc/eltorito.img"
mkdir -p "${ISO_DIR}/boot/grub/i386-pc"
if [ ! -f "$BIOS_ELTORITO" ]; then
  # try to find grub eltorito
  if [ -f "/usr/lib/grub/i386-pc/eltorito.img" ]; then
    cp "/usr/lib/grub/i386-pc/eltorito.img" "$BIOS_ELTORITO"
  else
    # create minimal: use grub-mkimage to create bios core
    log "Creating BIOS eltorito image via grub-mkimage..."
    grub-mkimage -O i386-pc -o "${ISO_DIR}/boot/grub/i386-pc/core.img" -p /boot/grub biosdisk iso9660 part_msdos part_gpt fat normal search search_fs_uuid linux linux16 configfile echo 2>&1 | head -20 || true
    cat "${GRUB_MBR}" "${ISO_DIR}/boot/grub/i386-pc/core.img" > "$BIOS_ELTORITO" 2>/dev/null || {
      # fallback: copy mbr as eltorito
      cp "${GRUB_MBR}" "$BIOS_ELTORITO" 2>/dev/null || dd if=/dev/zero of="$BIOS_ELTORITO" bs=1K count=4 2>/dev/null || true
    }
  fi
fi

# if efi.img exists and valid, add as alternative boot
if [ -f "${EFI_IMG}" ] && [ -s "${EFI_IMG}" ] && [ -f "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" ]; then
  XORRISO_ARGS+=(-eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot -isohybrid-gpt-basdat)
fi
XORRISO_ARGS+=(-o "${ISO_OUT}")
XORRISO_ARGS+=("${ISO_DIR}")

# fallback if BIOS args fail due to missing eltorito, try simpler command
set +e
log "Running: xorriso ${XORRISO_ARGS[*]}"
xorriso "${XORRISO_ARGS[@]}" 2>&1 | tee "${BUILD_DIR}/xorriso.log"
RC=$?
set -e
if [ $RC -ne 0 ]; then
  warn "Hybrid xorriso failed (RC=$RC), trying simpler ISO without MBR..."
  cat "${BUILD_DIR}/xorriso.log" | tail -40
  # simpler: use xorriso with only UEFI or only BIOS
  if [ -f "${EFI_IMG}" ] && [ -s "${EFI_IMG}" ]; then
    log "Trying UEFI-only ISO..."
    xorriso -as mkisofs -r -V "${ISO_LABEL}" -J -joliet-long -e boot/grub/efi.img -no-emul-boot -o "${ISO_OUT}" "${ISO_DIR}" 2>&1 | tee "${BUILD_DIR}/xorriso2.log" || true
  fi
  if [ ! -f "${ISO_OUT}" ] || [ ! -s "${ISO_OUT}" ]; then
    warn "Trying absolute fallback mkisofs-like..."
    xorriso -as mkisofs -r -V "${ISO_LABEL}" -J -o "${ISO_OUT}" "${ISO_DIR}" 2>&1 | tee "${BUILD_DIR}/xorriso3.log" || true
  fi
fi

if [ ! -f "${ISO_OUT}" ] || [ ! -s "${ISO_OUT}" ]; then
  err "ISO not created - check xorriso logs in ${BUILD_DIR}/xorriso*.log"
  exit 1
fi

# hybrid for USB dd
if command -v isohybrid >/dev/null 2>&1; then
  log "Making isohybrid..."
  isohybrid --uefi "${ISO_OUT}" 2>&1 | tail -5 || true
fi

# checksum and validation
log "=== Stage 9: Validation ==="
ls -lh "${ISO_OUT}"
du -sh "${ISO_OUT}"
SIZE=$(stat -c%s "${ISO_OUT}")
SIZE_MB=$((SIZE / 1024 / 1024))
log "ISO size: ${SIZE} bytes (${SIZE_MB} MB)"
# plausible size: 300MB min, 4GB max (GitHub artifact limit)
if [ "$SIZE" -lt $((300*1024*1024)) ]; then
  err "ISO too small (<300MB) - likely incomplete"
  exit 1
fi
if [ "$SIZE" -gt $((4096*1024*1024)) ]; then
  warn "ISO >4GB may exceed GitHub artifact limits"
fi

# checksum
( cd "${OUT_DIR}" && sha256sum JARVISOS.iso > JARVISOS.iso.sha256 )
cat "${OUT_DIR}/JARVISOS.iso.sha256"
# also md5
( cd "${OUT_DIR}" && md5sum JARVISOS.iso > JARVISOS.iso.md5; cat JARVISOS.iso.md5 )

# ISO integrity: check with 7z or xorriso
if command -v xorriso >/dev/null 2>&1; then
  log "Checking ISO structure with xorriso..."
  xorriso -indev "${ISO_OUT}" -pvd_info 2>&1 | head -40 || true
  xorriso -indev "${ISO_OUT}" -ls / -- 2>&1 | head -60 || true
fi
if command -v file >/dev/null 2>&1; then
  file "${ISO_OUT}" || true
fi

# bootloader check
if [ -f "${ISO_DIR}/EFI/BOOT/BOOTX64.EFI" ]; then
  log "✓ UEFI bootloader present"
else
  warn "UEFI bootloader missing"
fi
if [ -f "${ISO_DIR}/live/vmlinuz" ] && [ -f "${ISO_DIR}/live/filesystem.squashfs" ]; then
  log "✓ Live kernel + squashfs present"
else
  err "Live files missing"
  exit 1
fi

# make readable
chmod 644 "${ISO_OUT}" "${OUT_DIR}/JARVISOS.iso."* 2>/dev/null || true

# summary
cat <<SUMMARY

============================
 JARVISOS build complete
============================
 ISO: ${ISO_OUT}
 Size: ${SIZE_MB} MB
 SHA256: $(cat ${OUT_DIR}/JARVISOS.iso.sha256)
 Kernel: $(basename $KVER)
 Build dir: ${BUILD_DIR}
 Out dir: ${OUT_DIR}

 Next steps:
  - Test: ./scripts/validate-iso.sh ${ISO_OUT}
  - QEMU: qemu-system-x86_64 -enable-kvm -m 2048 -cdrom ${ISO_OUT} -boot d -bios /usr/share/OVMF/OVMF.fd (for UEFI)
  - Flash USB: sudo dd if=${ISO_OUT} of=/dev/sdX bs=4M status=progress; sync
  - Checksum verify: sha256sum -c ${OUT_DIR}/JARVISOS.iso.sha256

SUMMARY

log "Build finished successfully"
