# Building JARVISOS — Reproducible Local & Cloud

**You need:** for cloud, just a GitHub account + browser; for local, Debian 12, 10 GB free, `sudo`.  
**Produces:** `out/JARVISOS.iso` (≈900 MB–1.8 GB) + `out/JARVISOS.iso.sha256` + `build.log`.

---

## 1. Cloud build (recommended — your 4 GB laptop stays free)

GitHub runners (`ubuntu-22.04`) do the heavy `debootstrap` + `mksquashfs` + `xorriso`. You download the artifact.

### 1.1 First time — push & enable Actions

```bash
# from your jarvisos checkout
git status
git push origin arena/5a9160ac-jarvisos   # or main
# On github.com/mhammedamazil-hub/jarvisos:
#  Settings → Actions → General → “Allow all actions” (if disabled)
```

### 1.2 Trigger the build — step-by-step with clicks

1. Open **https://github.com/mhammedamazil-hub/jarvisos** in your browser (log in).
2. Click the **Actions** tab at the top (between Code and Issues).
3. In the left sidebar, click **Build JARVISOS** (the workflow name).
4. Click the **Run workflow** button on the right (blue-grey).
5. A dropdown appears:
   - **Use workflow from:** select `arena/5a9160ac-jarvisos` (or `main`).
   - **Fast build:** leave `false` (recommended for release) or `true` (gzip, ~20% faster, larger ISO) for iteration.
6. Click **Run workflow** (green).
7. A new row appears — click it. You’ll see two jobs: **Static checks** (~1 min) → **Build ISO** (~35–60 min) → **QEMU smoke boot test** (~3 min).
8. Click **Build ISO** to watch live logs. Expand **Install host build dependencies**, **Dry-run validation**, **Build JARVISOS ISO**. Look for:
   ```
   [02:34:12] ... debootstrap done
   [02:45:01] ... Installing 100 packages...
   [03:10:22] ✅ ISO size: 1422339584 bytes (1356 MB)
   [03:10:23] ✓ checksum verified
   ```
9. Wait until the run is **green ✓** (or red ✗ — see §4).
10. At the bottom of the run page, find **Artifacts** → click **JARVISOS-iso** → downloads a ZIP (`JARVISOS-iso.zip`).
11. Unzip on your Linux Mint laptop:
    ```bash
    unzip JARVISOS-iso.zip -d ~/Downloads/jarvisos-artifact
    ls -lh ~/Downloads/jarvisos-artifact/
    #  JARVISOS.iso (1.3 GB)
    #  JARVISOS.iso.sha256
    #  JARVISOS.iso.md5
    #  build.log
    ```
12. **Verify (mandatory):**
    ```bash
    cd ~/Downloads/jarvisos-artifact
    sha256sum -c JARVISOS.iso.sha256
    # JARVISOS.iso: OK
    # If OK fails, re-download — do not flash a corrupted ISO.
    ```
13. Artifacts are kept **14 days**; build logs 7 days.

> **Automatic triggers:** pushes to `main` or `arena/**` that touch `build/`, `config/`, `overlay/`, `scripts/`, or the workflow itself also start a build — but manual `workflow_dispatch` is the explicit way you control releases. The workflow **never** auto-publishes a GitHub Release or erases data.

### 1.3 Download without browser (CLI)

```bash
# install gh
sudo apt-get install gh
gh auth login
gh run list --workflow="Build JARVISOS" --limit 5
gh run download <RUN_ID> -n JARVISOS-iso -D out/
sha256sum -c out/JARVISOS.iso.sha256
```

---

## 2. Local build — Debian 12 host

### 2.1 Prereqs

```bash
# Host must be Debian 12 bookworm (the sandbox is), x86_64, ~10 GB free
df -h
free -h
uname -m   # must be x86_64

sudo apt-get update
sudo apt-get install -y debootstrap squashfs-tools xorriso \
  grub-pc-bin grub-efi-amd64-bin grub-common \
  mtools dosfstools isolinux syslinux-common \
  ovmf qemu-system-x86 ca-certificates curl rsync python3 file kmod
```

### 2.2 Validate before building (no root, fast)

```bash
git clone https://github.com/mhammedamazil-hub/jarvisos.git && cd jarvisos
# or: cd /home/user/jarvisos
./scripts/test-jarvis.sh
./scripts/check-packages.sh
sudo ./build/build.sh --dry-run   # actually runs as non-root too for dry-run
# expect:
# ✓ jarvis-center syntax OK
# ✓ no embedded secrets
# ✓ Config validation passed
# Dry run: would proceed to debootstrap...
```

### 2.3 Full build

```bash
sudo ./build/build.sh            # ~25–50 min depending on CPU/disk
# or faster, larger ISO:
sudo ./build/build.sh --fast

# outputs:
ls -lh out/
cat out/JARVISOS.iso.sha256
./scripts/validate-iso.sh out/JARVISOS.iso
```

**Build stages logged:** debootstrap → APT → overlay rsync → hooks → mksquashfs → xorriso → checksum → validation.

**Cache:** `build/work/apt-cache` is bind-mounted and reused; second build skips debootstrap unless `--clean`.

**Clean rebuild:**
```bash
sudo ./build/build.sh --clean
sudo ./build/build.sh
```

**Reproducibility knob:**
```bash
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct) sudo -E ./build/build.sh
```

### 2.4 What the script does (short)

See `build/build.sh` header (549 lines, `set -euo pipefail`). In order: `check_host_deps` → `validate_config` → `debootstrap --variant=minbase bookworm` → `mount proc/sys/dev` → write `sources.list` → `apt-get install $(cat config/package-lists/*.list.chroot)` → `rsync overlay/` → run `config/hooks/live/*.chroot` → `mksquashfs` → copy `vmlinuz`+`initrd` → `grub-mkstandalone` → `mkfs.vfat` + `mcopy` → `xorriso hybrid` → `sha256sum` → `xorriso -pvd_info`.

---

## 3. Verify the ISO — same locally or from cloud

```bash
# 1. checksum — must be OK
sha256sum -c out/JARVISOS.iso.sha256
# or without file:
sha256sum out/JARVISOS.iso   # compare to contents of .sha256

# 2. structure
./scripts/validate-iso.sh out/JARVISOS.iso
# ✓ size plausible (300 MB–4 GB, expect 900 MB–2 GB)
# ✓ UEFI BOOTX64.EFI present
# ✓ live filesystem.squashfs present
# ✓ kernel present

# 3. manual xorriso inspection
xorriso -indev out/JARVISOS.iso -pvd_info
xorriso -indev out/JARVISOS.iso -ls /live --

# 4. QEMU smoke (BIOS+UEFI, headless, 20s each)
./scripts/qemu-test.sh out/JARVISOS.iso
# timeout is expected — script checks El Torito, not GUI

# 5. JARVIS app offline
./scripts/test-jarvis.sh
# ✓ offline fallback present, no secrets
```

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| **Workflow red, “debootstrap failed”** | Debian mirror rate-limit or runner apt stale | Re-run workflow; check **Install host build dependencies** logs; no action on your side beyond retry |
| **“No kernel found in /boot”** | `linux-image-amd64` didn’t install (network) | Re-run with `--clean`; check `build/work/installed-packages.txt` |
| **“xorriso failed (RC=…)” hybrid** | Missing `isohdpfx.bin` | Workflow installs `grub-pc-bin`; locally `sudo apt-get install grub-pc-bin`. Build falls back to UEFI-only then plain ISO — check `build/work/xorriso.log` |
| **ISO too small (<300 MB)** | squashfs missing or truncated | `sudo ./build/build.sh --clean` + `df -h` (need 10 GB free) |
| **Artifact not uploaded (no file)** | ISO >2 GB hit compression | Workflow uses `compression-level: 0`; if still fails, use `--fast` or reduce `config/package-lists/apps.list.chroot` (remove `vlc`) |
| **No UEFI boot on laptop** | Secure Boot on | Dell 3558: F2 → Secure Boot → Disable; boot with F12 → USB |
| **QEMU “KVM not available”** | Runner has no `/dev/kvm` | Expected — smoke test runs without `-enable-kvm` on Actions |
| **Slow build on laptop** | `xz` compression CPU-heavy | Use `sudo ./build/build.sh --fast` (gzip, larger ISO, faster) |
| **Permission denied on ./build/build.sh** | not executable | `chmod +x build/build.sh scripts/*.sh` |
| **Workflow never starts** | push to wrong branch or path not matched | Push to `main` or `arena/*` and touch `build/`, `config/`, `overlay/`, or manually **Run workflow** |

**Where to look:**

- **Cloud:** Actions → run → **Build ISO** → expand logs; on failure, download **build-logs** artifact (`build.log`, `xorriso.log`).
- **Local:** `build/work/xorriso*.log`, `build.log` (tee), `out/JARVISOS.iso.sha256`.

**Never re-run an unchanged failing build >3 times.** Read the log first — `apt-get install` errors list the missing package; `xorriso` errors show the missing boot image.

---

## 5. Disk & time budget

| Build | Time | Disk peak | ISO | RAM needed |
|-------|------|-----------|-----|------------|
| Cloud (ubuntu-22.04, 2 vCPU) `xz` | 35–60 min | ~8 GB (chroot 3 GB + squash 1.5 GB + ISO 1.3 GB + cache) | 1.1–1.6 GB | 7 GB runner |
| Cloud `--fast` (gzip) | 20–35 min | same, but ISO larger | 1.4–2.0 GB | same |
| Local Dell 3558 (`--fast`) | 30–50 min | 8 GB free recommended | same | 4 GB host works (use swap if needed) |
| Dry-run | <10 s | 0 | 0 | — |

Runner disk: workflow does `rm -rf /usr/share/dotnet /opt/ghc` before build to free ~6 GB.

---

## 6. Pinning, caching, security

- **Actions pinned:** `actions/checkout@v4` (11bd719…), `actions/cache@v4`, `actions/upload-artifact@v4`, `actions/download-artifact@v4` — update by SHA when rotating.
- **Permissions:** `contents: read` only; no `write` unless a future release job is added (separate, manual).
- **Secrets:** workflow never prints `secrets.*`; overlay/build scan blocks `sk-…` keys. Users set keys after install in `~/.config/jarvis/config.json`.
- **Cache keys:** `apt-${{ hashFiles('config/package-lists/*.list.chroot') }}-${{ runner.os }}` — `restore-keys` fallback to `apt-*`. Cache is **not** used for build outputs (no stale ISO).

---

## 7. Releasing (explicit, separate)

The current workflow **does not** create GitHub Releases. When you have a hardware-validated ISO (status 4/5):

1. Tag: `git tag v0.1.0 && git push origin v0.1.0`
2. Add a separate `release.yml` workflow (not auto) or manually create a Release in the GitHub UI and attach `out/JARVISOS.iso` + `JARVISOS.iso.sha256` (drag-drop, <2 GB limit). For >2 GB, use `gh release upload`.
3. Never overwrite a known-good Release — create a new tag.

---

## 8. Reproducible build checklist

- [ ] `git status` clean, commit SHA recorded (`git log -1 --oneline`)
- [ ] `SOURCE_DATE_EPOCH` set from commit date
- [ ] `debootstrap` variant `minbase` same suite `bookworm`
- [ ] `config/package-lists/*.list.chroot` unchanged between builds (diff them)
- [ ] `overlay/` perms preserved (`rsync -a`)
- [ ] `mksquashfs -comp xz -Xdict-size 100%` fixed (or `--fast` gzip documented)
- [ ] `sha256sum` recorded and `validate-iso.sh` passes
- [ ] `build/work/installed-packages.txt` saved as artifact
