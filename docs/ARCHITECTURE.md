# JARVISOS Architecture

**Target:** Dell Inspiron 3558 — x86_64, UEFI, 4 GB RAM, Intel iGPU. Stability > eye candy.  
**Date:** 2026-10-14 — initial engineering baseline (no hardware boot yet, design frozen).

---

## 1. Base strategy — one coherent path

**Chosen:** `debootstrap` (variant `minbase`) + chroot install + `mksquashfs` + `xorriso` hybrid ISO.  
**Rejected:** `live-build` wrapper — while canonical for Debian, it couples config templating, caching, and `lb build` magic that is hard to debug on GitHub runners and hard to reproduce without its own caches. Our manual pipeline is **explicit**: every step logs, fails fast, and is re-runnable with `--clean`. It still uses the same *established* tools Debian live uses under the hood.

- **Suite:** Debian 12 `bookworm` stable — security-supported, older kernel (6.1) matches 3558’s Intel hardware, small memory footprint. `trixie` would be newer but larger and less tested on 4 GB.
- **Mirror:** `deb.debian.org/debian` + `security.debian.org` + `-updates` + `-backports` (firmware).
- **Variant:** `minbase` → install only what `config/package-lists/*.list.chroot` lists. No `tasksel`, no recommends.
- **Reproducibility:** `SOURCE_DATE_EPOCH` from `git log -1 --pretty=%ct`, `DEBIAN_FRONTEND=noninteractive`, apt cache bind-mount, deterministic `mksquashfs` flags.

```
debootstrap bookworm (minbase)
  → chroot apt install  (base + desktop + apps + jarvis + firmware)
    → rsync overlay/  (skel, lightdm, jarvis-center, grub.cfg)
      → hooks/live/*.chroot (user, systemd, d-bus)
        → mksquashfs → filesystem.squashfs
          → iso tree (/live/vmlinuz+initrd, /boot/grub, /EFI/BOOT/BOOTX64.EFI)
            → xorriso hybrid ISO (BIOS El Torito + UEFI efi.img)
              → sha256 + validation
```

---

## 2. Boot chain

### 2.1 BIOS + UEFI hybrid

- **BIOS:** `grub-pc-bin` `isohdpfx.bin`/`boot_hybrid.img` as MBR, `eltorito.img` (biosdisk+iso9660+search) as El Torito no-emul. `isohybrid --uefi` post-process for USB `dd`.
- **UEFI:** `grub-efi-amd64-bin` → `grub-mkstandalone -O x86_64-efi -o EFI/BOOT/BOOTX64.EFI` with embedded `search --label JARVISOS` → `/boot/grub/grub.cfg`. FAT image `boot/grub/efi.img` (12 MB, mkfs.vfat, mtools) holds `BOOTX64.EFI` and is added as `-eltorito-alt-boot -e boot/grub/efi.img -isohybrid-gpt-basdat`.
- **GRUB config:** `/boot/grub/grub.cfg` lives both in ISO root and in `efi.img`. Three entries: quiet, verbose, failsafe. Kernel cmdline: `boot=live quiet splash components username=jarvis hostname=jarvisos noeject fetch=http:// net.ifnames=0` — `live-boot` handles overlayfs, `fetch=http://` is a no-op but keeps hook extensibility.
- **Validation:** `xorriso -report_el_torito as_mkisofs` must show El Torito, `-ls /EFI/BOOT` must show `BOOTX64.EFI`.

### 2.2 Live system

- **Init:** `live-boot` + `live-config` + `systemd`. Kernel + initrd copied from `/boot/vmlinuz-*`/`initrd.img-*` (newest) into `/live/`.
- **Squashfs:** `mksquashfs chroot iso/live/filesystem.squashfs -comp xz -Xdict-size 100%` (or gzip level 1 for `--fast`). Excludes `var/cache/apt`, `tmp`, `proc`, `sys`, `dev`. Size target: 900 MB–1.8 GB (Firefox ESR dominates).
- **Persistence:** none yet (live-only). Future: optional `live-persistence` partition.

---

## 3. Desktop — lightweight, 4 GB conscious

| Layer | Choice | Why |
|-------|--------|-----|
| X | `xorg` + `xserver-xorg-video-all` + `xserver-xorg-input-all` | Broad Intel fallback, no Wayland compositor overhead |
| WM | `openbox` + `obconf` | ~15 MB, keyboard-driven, XML rc.xml, no animations |
| Panel | `tint2` | ~10 MB, systray + clock + launcher, no GNOME shell |
| Login | `lightdm` + `lightdm-gtk-greeter` | Autologin `jarvis:jarvis`, `autologin-session=openbox` |
| Icons/theme | `Papirus-Dark` + `Adwaita-dark` + `arc-theme` | Dark navy matches JARVIS, small |
| Notifications | `dunst` | Lightweight |
| Polkit | `lxpolkit` | Auth dialogs |
| No compositor | `picom` omitted | Saves RAM/GPU; `hsetroot -solid #0a1128` only |

**Openbox keys** (`rc.xml`): `Super+Space`/`Alt+F1` → `jarvis-center`, `Super+T` → `lxterminal`, `Super+E` → `pcmanfm`, `Super+B` → `firefox-esr`, `Alt+Tab` window switch, `Super+Q` close. Mouse dragThreshold 8px.

**Autostart** (`~/.config/openbox/autostart`): sets navy background, starts `tint2`, `nm-applet`, `blueman-applet`, `dunst`, then `sleep 2 && jarvis-center`.

**tint2rc:** bottom center, 36 px, launchers for JARVIS/Files/Terminal/Browser, clock with `jarvis-center` on click, systray 18 px, battery hide 98%.

**Memory budget:** idle after boot ~220 MB (X + Openbox + tint2 + nm-applet) + 90 MB JARVIS + 0–300 MB Firefox → fits 4 GB with headroom.

---

## 4. Applications — justified light picks

- **File manager:** `pcmanfm` (GTK, ~20 MB) not Nautilus
- **Terminal:** `lxterminal` (LXDE, tiny) not GNOME Terminal
- **Browser:** `firefox-esr` — supported, but the biggest package (~180 MB squashfs). No Chromium duplicate. Could swap to `falkon` for <50 MB but loses extension support; ESR retained for user familiarity.
- **Editor:** `mousepad` (Xfce, light)
- **Archiver:** `xarchiver` + `file-roller` fallback
- **Calculator:** `galculator`, **Clipboard:** `parcellite`, **Screenshot:** `scrot`
- **Multimedia:** `vlc` (codec-complete, but large; could be removed for minimal ISO)
- **System:** `NetworkManager` + `nm-connection-editor` + `wpasupplicant`, `pulseaudio` + `pavucontrol` + `pulseaudio-module-bluetooth`, `bluez` + `blueman`, `gparted`, `htop`, `neofetch`, `rsync`
- **Firmware:** `firmware-linux-free`, `firmware-misc-nonfree`, `firmware-iwlwifi`/`realtek`/`atheros`, `intel-microcode`

---

## 5. JARVIS command interface

### 5.1 Design constraints

- No embedded ML, no Electron, no 500 MB frameworks. Pure Python 3 + PyGObject + GTK3 (`gir1.2-gtk-3.0`) — already in Debian main, ~30 MB deps.
- Dark navy `#0a1128`/`#0d1b2a`/`#111e3a`, cyan `#00d4ff` focus, rounded 12 px entries, no shadows/animations.
- Centered window 760×560, title “JARVIS COMMAND CENTER”, subtitle “JARVISOS”.
- Central entry with placeholder “What would you like me to do?” — `activate` → `on_command`.
- Mic button placeholder (shows offline unless `voice_enabled` + key + network).
- Launcher: 8 quick tiles (Files/Terminal/Browser/Settings/Editor/Network/Audio/Bluetooth) + searchable `FlowBox` of all `*.desktop` files via `Gio.DesktopAppInfo`.
- System tab: reads `/proc/meminfo`, `/proc/loadavg`, `/proc/uptime`, `nmcli device status`, `df -h /` — refresh every 5 s.
- Settings tab: `ComboBoxText` provider (`none`, `openai`, `anthropic`, `ollama`, `custom`), `api_key` hidden entry (`chmod 600 ~/.config/jarvis/config.json`), `api_base`, `model`, `voice_enabled` checkbox, Save/Test/Clear.
- Footer: memory-optimized help + keyboard hints.

### 5.2 Intelligence

- **Defaults:** `ai_provider=none`, `api_key=""`, `offline_mode=true`.
- **Config sources:** `~/.config/jarvis/config.json` (user, 600) → `/etc/jarvis/config.json` (system) → env `$JARVIS_API_KEY`/`$JARVIS_API_PROVIDER`. JSON only, never shell.
- **Cloud path:** `ai_query()` checks `provider`/`key`; if missing → returns offline fallback string; if present → reports “would send to {provider} with model {model}” but **does not auto-call network** in live ISO (user must type prompt and have network). Prevents accidental spend and live-ISO exfiltration.
- **Offline fallback:** always-available: bottom response label says “Offline mode ready. Configure AI in Settings…”, `help`/`system info`/`open <app>` work.
- **Computer control:** `ALLOWED_LAUNCHERS` whitelist, `find_desktop_apps()` via Gio, `launch_app()` via `appinfo.launch()`, fallback `subprocess.Popen`. Destructive detection `is_destructive()` scans for `rm -rf`, `mkfs`, `dd if=`, `shutdown`, etc. → `Gtk.MessageDialog` OK/Cancel before execute (least privilege).
- **Stop:** `Esc` clears, `Ctrl+Q` quits, dialog Cancel aborts. No background stuck jobs.
- **Secrets hygiene:** build and `scripts/test-jarvis.sh` grep for `sk-` and fail; workflow has `if: failure()` log upload without secrets.

### 5.3 Why GTK not web

A web UI would need a browser engine in the shell (CEF/Electron) → hundreds of MB, GPU, fails offline. GTK3 is native, themeable, and already required by `pcmanfm`/`lxterminal` — zero extra cost.

---

## 6. Networking, audio, Bluetooth

- **NetworkManager** enabled via `systemctl enable NetworkManager` in hook and chroot. `nm-applet` in tray, `nmcli` for CLI. WiFi drivers from `firmware-iwlwifi` etc.
- **PulseAudio** (not PipeWire) — lower RAM, `pavucontrol` for mixer. `pulseaudio-module-bluetooth` for A2DP.
- **BlueZ** + `blueman-applet` — lightweight BT manager.

---

## 7. Users and security

- **Live user:** `jarvis` (`jarvis:jarvis`), groups `sudo,audio,video,netdev,bluetooth,plugdev`, `NOPASSWD` sudo in live (convenient; installer would lock).
- **Recovery:** `jarvis-recovery` script + `jarvis-recovery --check`, TTY `Ctrl+Alt+F2`.
- **Polkit:** `lxpolkit` for GUI privilege.
- **No embedded keys:** scanned at build, lint, and before commit.

---

## 8. Build reproducibility

- `SOURCE_DATE_EPOCH` from Git, `DEBIAN_FRONTEND=noninteractive`, no timestamps inside squashfs beyond file mtimes.
- `build/work/apt-cache` bind-mounted and cached in Actions (`hashFiles('config/package-lists/*.list.chroot')`).
- `mksquashfs` flags fixed; overlay is `rsync -a` (perms preserved).

---

## 9. Alternatives considered

| Decision | Alternative | Why not |
|----------|-------------|---------|
| bookworm vs trixie | trixie (testing) | Bigger, less QA on old Intel |
| Openbox vs Xfce | Xfce | +200 MB, heavier panel |
| LightDM vs SDDM | SDDM | Qt, heavier |
| PulseAudio vs PipeWire | PipeWire | More deps, newer, less stable on bookworm live |
| Firefox ESR vs Falkon | Falkon | Lighter but weak extension/DRM |
| debootstrap manual vs live-build | live-build | Less transparent, harder to cache/debug |

---

## 10. Future extension points

- **Installer:** `calamares` or `debian-installer` live wrapper — not yet, live-only.
- **Persistence:** `live persistence` + overlay partition.
- **Ollama local:** optional large download, disabled by default for 4 GB.
- **Voice:** PipeWire + Whisper tiny (if RAM allows) — currently placeholder.
- **OTA:** `mender` or apt repo.

---

## 11. Diagrams (text)

```
[Dell 3558 UEFI] → [GRUB BOOTX64.EFI] → [grub.cfg] → [vmlinuz + initrd.img]
                                                            ↓
                                                     [live-boot → overlayfs]
                                                            ↓
                                                   [filesystem.squashfs]
                                                            ↓
                                            [systemd → lightdm → openbox]
                                                            ↓
                                          [tint2 + pcmanfm + firefox + jarvis-center]
```

```
[JARVIS Center]
  ┌─────────────────────────────────────┐
  │ JARVIS  COMMAND CENTER   ● Offline  │
  │ ─────────────────────────────────  │
  │ [ What would you like me to do?  ◉ Mic ↵] │
  │ Launcher | System | Settings       │
  │ [Files][Terminal][Browser][Editor] │
  │ All apps: [Search________]         │
  │ System: Mem, Load, Uptime, Disk    │
  │ Settings: Provider, Key (600), Model│
  │ ─────────────────────────────────  │
  │ Response: “Offline ready…”         │
  └─────────────────────────────────────┘
```
