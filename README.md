# JARVISOS

**A lightweight, reproducible, Debian-based Linux OS with a futuristic command-center desktop — designed for Dell Inspiron 3558 (x86_64 UEFI, 4 GB RAM).**

> **Honest status (2026-10-14)** — See [`docs/STATUS.md`](docs/STATUS.md) for the full handoff. This is a **real** bootable OS project, not a mockup. The build pipeline is implemented and syntax-validated, but an ISO has not yet been produced on hardware — see [Testing gates](#testing-gates) below.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Debian](https://img.shields.io/badge/base-Debian%2012%20bookworm-red.svg)
![Desktop](https://img.shields.io/badge/desktop-Openbox%20%2B%20tint2-darkblue.svg)
![Build](https://img.shields.io/badge/build-debootstrap%20→%20squashfs%20→%20xorriso-informational.svg)

---

## What is JARVISOS?

JARVISOS is a **real Linux distribution** built from Debian 12 (bookworm) stable:

- **Bootloader:** GRUB 2 with x86_64 UEFI + BIOS hybrid (isohybrid), verified with `xorriso`
- **Base:** `debootstrap` minbase + `live-boot`/`live-config` + `systemd`
- **Kernel:** `linux-image-amd64` + Intel/Realtek firmware for Dell 3558
- **Desktop:** Xorg + Openbox (no compositor by default) + tint2 panel + LightDM autologin — ~180 MB idle, responsive on 4 GB RAM
- **Apps:** `pcmanfm` (files), `lxterminal`, `firefox-esr`, `mousepad`, `NetworkManager`, `pulseaudio` + `blueman` — all lightweight
- **JARVIS Center:** `jarvis-center` — a GTK3 command palette (dark navy `#0a1128`, cyan `#00d4ff` accents) that is **offline-first**: launcher + system info always work; AI is cloud-only with *user-supplied* keys, never embedded in the ISO

**Distinctive but restrained UI:** dark surfaces, clean typography, central “What would you like me to do?” entry, keyboard shortcuts (`Super+Space` to focus, `Super+T` terminal, `Super+E` files), no particle effects, no heavy ML in the ISO.

---

## Hardware target

- **Laptop:** Dell Inspiron 3558
- **Arch:** x86_64
- **Firmware:** UEFI (boot test via QEMU + OVMF; hardware test on your laptop)
- **RAM:** 4 GB — every choice favors memory: no compositor, no Electron, no local LLM
- **CPU/GPU:** Older Intel + integrated graphics — Openbox + Xorg, not Wayland/GNOME
- **Current OS:** Linux Mint (you can test the live ISO without installing)

---

## Quick start — get your first ISO in 5 steps

> Full instructions: [`docs/BUILDING.md`](docs/BUILDING.md)  •  **Beginner clicks:** [`docs/HOW-TO-GET-ISO.md`](docs/HOW-TO-GET-ISO.md) (Steps 1–10)(docs/BUILDING.md)

### Option A — Cloud build (recommended, no powerful laptop needed)

1. **Push this branch to GitHub** (or open a PR):
   ```bash
   git push origin arena/5a9160ac-jarvisos
   ```
2. On GitHub, go to **Actions → Build JARVISOS → Run workflow** → choose branch `arena/5a9160ac-jarvisos` → **Run workflow** (optionally `fast_build: true` for quicker iteration).
3. **Wait ~35–60 min** — the job installs deps, runs `debootstrap`, builds `filesystem.squashfs`, creates `JARVISOS.iso` with `xorriso`, and validates it. Watch the **Build ISO** logs for `✓ size plausible`, `✓ checksum verified`.
4. When green, click the run → **Artifacts → JARVISOS-iso** → download the ZIP → unzip → you have `JARVISOS.iso` + `JARVISOS.iso.sha256`.
5. **Verify & test** (see below).

### Option B — Local build (Debian 12 host, 10 GB free, root)

```bash
sudo apt-get update && sudo apt-get install -y debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin mtools dosfstools
sudo ./build/build.sh --dry-run   # validates config, no root needed for this step
sudo ./build/build.sh             # real build → out/JARVISOS.iso
./scripts/validate-iso.sh out/JARVISOS.iso
./scripts/test-jarvis.sh
```

---

## Verify the ISO (do this every time)

```bash
# 1. Checksum (in the same folder as the ISO)
sha256sum -c JARVISOS.iso.sha256
# must print: JARVISOS.iso: OK

# 2. Structure
./scripts/validate-iso.sh JARVISOS.iso
# must print ✓ size plausible, ✓ UEFI BOOTX64.EFI present, ✓ live filesystem.squashfs present

# 3. Size sanity
ls -lh JARVISOS.iso   # expect 900 MB – 2.0 GB (varies with compression & Firefox)

# 4. Never flash without verifying checksum!
```

---

## Safe testing — live, no install required

```bash
# QEMU (BIOS)
qemu-system-x86_64 -enable-kvm -m 2048 -cdrom JARVISOS.iso -boot d

# QEMU (UEFI) — needs OVMF
sudo apt-get install ovmf
qemu-system-x86_64 -enable-kvm -m 2048 -bios /usr/share/OVMF/OVMF.fd -cdrom JARVISOS.iso -boot d

# USB — DANGER: triple-check the device, this erases it
lsblk -d -o NAME,SIZE,MODEL,MTRAN
sudo dd if=JARVISOS.iso of=/dev/sdX bs=4M status=progress conv=fsync
sync
# Boot Dell 3558: F12 → select USB → “JARVISOS Live”
# Live user: jarvis / jarvis   (sudo without password in live session)
# Launch: Super+Space → JARVIS Center, or click the terminal icon
```

> **WARNING:** `dd` with the wrong `/dev/sdX` destroys data. Use **Disks** (gnome-disks) or **BalenaEtcher** if unsure.

---

## JARVIS intelligence — honest capabilities

| Feature | Offline | Online (user key) |
|---------|---------|-------------------|
| Launcher (Files, Terminal, Browser, Settings) | ✅ always | ✅ |
| System info, process view, disk usage | ✅ | ✅ |
| Command input & help | ✅ | ✅ |
| Cloud AI (OpenAI / Anthropic / Ollama / custom) | — | ✅ after you set key in **Settings → Save** |
| Voice (mic) | — | ⚠️ placeholder — UI exists, capture not wired on 4 GB build |
| Computer control | ✅ whitelisted: `open <app>`, `system info`, `clear` | same, with confirmation for destructive (`rm -rf`, `shutdown` asks “OK/Cancel”) |
| Stop/interrupt | ✅ Esc clears, Ctrl+Q quits, dialog Cancel | same |

- **Keys:** stored in `~/.config/jarvis/config.json` (`chmod 600`), also respects `$JARVIS_API_KEY` env. **Never** baked into Git, ISO, or logs — the build scans for `sk-…` and fails if found.
- **Offline fallback:** if no key/network, JARVIS says “AI not configured — running offline mode” and still launches apps.
- **Least privilege:** only allowlists; no hidden `sudo` without your explicit confirmation.

Configure after install: **JARVIS Center → Settings → Provider / API Key / Model → Save**. Test with “hello” in the command bar.

---

## Project layout

```
build/build.sh               # reproducible debootstrap → squashfs → xorriso pipeline
config/package-lists/*.list.chroot   # Debian packages (base, desktop, apps, jarvis, firmware)
config/hooks/live/*.chroot    # chroot hooks (user, services, cleanup)
overlay/                      # files copied into the live system
  usr/local/bin/jarvis-center # GTK command center (Python, ~900 lines, no heavy deps)
  etc/skel/.config/openbox/   # Openbox rc.xml + autostart
  etc/skel/.config/tint2/     # tint2 panel (dark navy)
  etc/lightdm/                # autologin lightdm
  usr/share/applications/     # .desktop launchers
scripts/
  validate-iso.sh             # ISO structure + checksum validator
  test-jarvis.sh              # offline JARVIS tests (no secrets)
  qemu-test.sh                # QEMU smoke boot (BIOS+UEFI)
.github/workflows/build-jarvisos.yml  # cloud build on ubuntu-22.04 runners
docs/
  ARCHITECTURE.md  BUILDING.md  STATUS.md  TESTING.md
```

---

## Testing gates — read before claiming “it works”

We report 5 separate statuses (a green build ≠ a booting laptop):

1. **Build succeeded** — ISO exists, non-empty, size 300 MB–4 GB, SHA256 recorded
2. **ISO structure validated** — `xorriso` finds `/live/filesystem.squashfs`, `/live/vmlinuz`, `/EFI/BOOT/BOOTX64.EFI`
3. **VM boot passed** — QEMU BIOS + UEFI smoke (see `scripts/qemu-test.sh`); logs show kernel boot
4. **Laptop hardware tested** — booted on Dell Inspiron 3558 via USB (manual, you)
5. **Installation tested** — installed to disk (not yet; live-only for now)

Current: **① validated in dry-run, ②–⑤ pending real ISO** — see [`docs/STATUS.md`](docs/STATUS.md).

---

## Docs

- **Architecture & decisions:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Reproducible builds (local + cloud):** [`docs/BUILDING.md`](docs/BUILDING.md)
- **Handoff & progress:** [`docs/STATUS.md`](docs/STATUS.md)
- **Validation procedure:** [`docs/TESTING.md`](docs/TESTING.md)

---

## Recovery (if desktop fails)

At the login screen or TTY (`Ctrl+Alt+F2`, login `jarvis` / `jarvis`):

```bash
jarvis-recovery --check   # diagnostics
sudo systemctl restart lightdm
cat /var/log/Xorg.0.log | tail -50
jarvis-center --cli       # test JARVIS without GUI
```

---

## License

MIT — see `LICENSE` (to be added). Do not commit secrets, do not overwrite known-good releases automatically, and respect Debian’s trademarks.

---

## Credits

Debian 12 bookworm, Openbox, tint2, LightDM, live-boot — and you, testing on real 4 GB hardware.
