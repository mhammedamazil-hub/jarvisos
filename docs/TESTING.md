# JARVISOS Testing — Exact Procedure & Results

**Principle:** never claim a level passes until its checks are green. One level passing does not imply higher levels.

```
1. Build succeeded
2. ISO structure validated
3. VM boot passed
4. Laptop hardware tested (Dell 3558)
5. Installation tested
```

---

## 0. Test matrix — what runs where

| Test | Command | Runs on | Needs | Expected |
|------|---------|---------|-------|----------|
| Shell syntax | `bash -n build/build.sh && bash -n config/hooks/live/*.chroot && bash -n scripts/*.sh` | Actions `lint` + local | bash | no errors |
| Python syntax | `python3 -m py_compile overlay/usr/local/bin/jarvis-center` | lint + local | python3 | OK |
| No secrets | `! grep -R "sk-[A-Za-z0-9]\{20\}" overlay/ config/ build/` | lint | grep | no hits |
| Package list sanity | `grep -v "^#" config/package-lists/*.list.chroot \| grep -v "^\s*$" \| wc -l` → total ≥20 | lint | bash | ≥20 |
| Config validation | `sudo ./build/build.sh --dry-run` | Actions + local | — | `✓ Config validation passed` |
| JARVIS offline | `./scripts/test-jarvis.sh` | local + chroot | python3, gi (optional) | `✓ offline fallback present` |
| ISO exists/size | `stat -c%s out/JARVISOS.iso` 300MB–4GB | Actions + local | file | plausible |
| SHA256 | `sha256sum -c out/JARVISOS.iso.sha256` | Actions + local | sha256sum | `OK` |
| ISO structure | `./scripts/validate-iso.sh out/JARVISOS.iso` | Actions + local | xorriso | `✓ UEFI`, `✓ live filesystem` |
| xorriso El Torito | `xorriso -report_el_torito as_mkisofs` | Actions + local | xorriso | El Torito entry |
| QEMU BIOS smoke | `./scripts/qemu-test.sh` (20s timeout) | Actions `smoke-boot` + local | qemu, ovmf | no panic, timeout expected |
| QEMU UEFI smoke | same with `-bios /usr/share/OVMF/OVMF.fd` | same | ovmf | timeout expected |
| Live desktop (manual) | Boot USB → LightDM → Openbox → `jarvis-center` | Dell 3558 | USB, F12 | desktop + JARVIS |
| Network/Bluetooth | `nmcli device wifi list`, pair BT | Dell 3558 | HW | connects |
| AI config (no real key) | Settings → dummy key → `Test` → “would connect” | Dell 3558 | — | no network call, no leak |

---

## 1. How to run — step by step

### 1.1 Static + offline (no root, <30 s)

```bash
cd /home/user/jarvisos   # or your checkout
bash -n build/build.sh && echo "✓ build.sh syntax"
bash -n scripts/*.sh config/hooks/live/*.chroot && echo "✓ hooks/scripts syntax"
python3 -m py_compile overlay/usr/local/bin/jarvis-center && echo "✓ jarvis-center syntax"
! grep -R "sk-[A-Za-z0-9]\{20\}" overlay/ config/ build/ 2>/dev/null && echo "✓ no secrets"
./scripts/test-jarvis.sh
./scripts/check-packages.sh
sudo ./build/build.sh --dry-run   # also works without sudo for dry-run
```

**Pass criteria:** all `✓`, no `FAIL`.

### 1.2 Build + ISO validation (needs root or Actions, 35–60 min)

**Cloud (Artifacts):**
1. Actions → Build JARVISOS → Run workflow → wait green.
2. Download **JARVISOS-iso** → unzip → `sha256sum -c JARVISOS.iso.sha256` → `OK`.
3. Click the run → **Build ISO** → logs show `ISO size: … MB`, `xorriso -pvd_info` shows `JARVISOS`.
4. Artifacts contain `JARVISOS.iso`, `JARVISOS.iso.sha256`, `build.log`, `installed-packages.txt`.

**Local:**
```bash
sudo ./build/build.sh            # or --fast
./scripts/validate-iso.sh out/JARVISOS.iso
# expect:
# Size: 1422339584 bytes (1356 MB)
# ✓ size plausible
# ✓ sha256 verified
# ✓ UEFI BOOTX64.EFI present
# ✓ live filesystem.squashfs present
# ✓ kernel present
cat out/JARVISOS.iso.sha256
file out/JARVISOS.iso   # “ISO 9660 ... bootable”
```

**Pass criteria:** ISO exists, non-empty, size 300 MB–4 GB, `file` says ISO 9660, `validate-iso.sh` prints all `✓`, checksum `OK`.

### 1.3 VM boot smoke (QEMU, headless, 1–2 min)

```bash
sudo apt-get install -y qemu-system-x86 ovmf xorriso
./scripts/qemu-test.sh out/JARVISOS.iso
# Both BIOS and UEFI attempts should time out without “no bootable device” or kernel panic in the head.
# Script prints:
#   xorriso -report_el_torito as_mkisofs  → El Torito catalog
#   BIOS smoke attempted (timeout is expected)
#   UEFI smoke attempted
#   Limitations: does NOT mean desktop works
```

On Actions, the `smoke-boot` job does the same with 90 s timeouts.

**Pass criteria:** `xorriso -report_el_torito` shows boot catalog; QEMU does not exit with `No bootable device`; boot is **plausible**. Report as **level 3 passed** only if you see kernel messages in the 90 s head. Otherwise report: *“ISO is bootable per xorriso; QEMU smoke timed out — graphical boot not confirmed.”*

### 1.4 Hardware test — Dell Inspiron 3558 (manual, you)

**Flash (dangerous, triple-check):**
```bash
lsblk -d -o NAME,SIZE,MODEL,MTRAN
# identify USB, e.g., /dev/sdb (NOT /dev/sda your Mint!)
sha256sum -c JARVISOS.iso.sha256
sudo dd if=JARVISOS.iso of=/dev/sdX bs=4M status=progress conv=fsync
sync
# or use Disks → Restore Disk Image, or BalenaEtcher
```

**Boot:**
1. Insert USB, reboot, press **F12** repeatedly → Boot Menu → select USB UEFI partition.
2. If Secure Boot error: F2 → Secure Boot → Disabled.
3. GRUB menu → **JARVISOS Live**.
4. Wait 30–60 s → LightDM → Openbox + tint2 bottom bar + navy background.
5. **JARVIS:** `Super+Space` (or click terminal icon → `jarvis-center`) → window centered, entry “What would you like me to do?” → type `help` → `open terminal` → `system info`.
6. **Network:** click nm-applet → WiFi → connect.
7. **Audio:** `pavucontrol`, **Bluetooth:** `blueman-manager`.
8. **Settings:** JARVIS → Settings → set Provider to `none` (offline) → try `open files` — must work offline.
9. **Destructive guard:** type `shutdown` → dialog “Confirm?” → Cancel → must not shut down.

**Recovery if black screen:** `Ctrl+Alt+F2` → `jarvis` / `jarvis` → `jarvis-recovery --check` → `sudo systemctl restart lightdm` → `cat /var/log/Xorg.0.log | tail -50`.

**Pass criteria:** desktop appears without manual xorg fix; JARVIS launches offline; applets show; `shutdown` guard triggers. Document: kernel, time, BIOS version, what failed.

### 1.5 AI config without real credentials (no network leak)

```bash
# inside live session, no network required
jarvis-center  # → Settings
# Provider: openai, API Key: test-dummy-123 (not real), Model: gpt-4o, Save
# Type in command bar: “hello”
# Expect: “AI provider ‘openai’ configured. Network query not executed…” (no network call)
# Check no key in logs:
grep -r "test-dummy" /var/log/ 2>/dev/null || echo "✓ no leak"
cat ~/.config/jarvis/config.json   # perms 600, key stored locally
```

**Pass criteria:** dummy key saved 600, no outbound request, offline message shown, `grep sk-` still clean.

---

## 2. Automated in CI — mapping

| CI job | Steps | Corresponds to manual § |
|--------|-------|-------------------------|
| `lint` | shellcheck, py_compile, no secrets, package counts | 1.1 |
| `build` | `debootstrap`, `mksquashfs`, `xorriso`, size/checksum, `xorriso -pvd_info -ls`, artifact upload | 1.2 |
| `smoke-boot` | download artifact, `sha256sum -c`, `qemu` BIOS+UEFI with timeout, `report_el_torito` | 1.3 |
| *(not yet)* hardware | — | 1.4 (you) |

Permissions `contents: read`, no auto-release, artifacts 14 days.

---

## 3. Results — 2026-10-14 (this sandbox)

### 3.1 Static + offline — executed here

```
$ bash -n build/build.sh && echo "✓ build.sh syntax"
✓ build.sh syntax
$ bash -n config/hooks/live/*.chroot scripts/*.sh && echo "✓ hooks/scripts syntax"
✓ hooks/scripts syntax
$ python3 -m py_compile overlay/usr/local/bin/jarvis-center && echo "✓ jarvis-center syntax"
✓ jarvis-center syntax
$ ! grep -R "sk-[A-Za-z0-9]\{20\}" overlay/ config/ build/ && echo "✓ no secrets"
✓ no secrets
$ sudo ./build/build.sh --dry-run
[02:32:...] Checking host dependencies... (would install debootstrap etc in sandbox without apt)
[02:32:...] Validating package lists... total 100 packages
[02:32:...] ✓ Config validation passed
[02:32:...] Dry run: would proceed to debootstrap...
$ ./scripts/test-jarvis.sh
✓ jarvis-center syntax OK
✓ no embedded secrets
✓ CLI test (GTK not available in minimal container, syntax passed)
✓ config write OK (dummy key, not real) 600
✓ destructive guard present
✓ offline fallback present
✓ jarvis logic checks passed
$ ./scripts/check-packages.sh
WARN: some bookworm packages not found in this host's Ubuntu sources (expected, not failing)
```

**Level 1:** dry-run passes but **no real ISO built in this sandbox** — it is a minimal container without `debootstrap`/`xorriso` and with <2 GB free; real build needs `sudo` + 10 GB + Debian bookworm host or GitHub runner.

### 3.2 Build + ISO validation — not run here (see STATUS)

- No `out/JARVISOS.iso` in this checkout yet.
- Workflow `.github/workflows/build-jarvisos.yml` is **present, syntax-valid, and ready to Run**.
- Next action: **you** trigger **Actions → Build JARVISOS → Run workflow** (see BUILDING.md §1.2) → expect green Build + artifact.

### 3.3 VM boot — pending ISO

- `scripts/qemu-test.sh` and CI `smoke-boot` are implemented.
- No boot to report until ISO exists. When it does, run: `./scripts/qemu-test.sh out/JARVISOS.iso` and record the 20-line QEMU head + `xorriso -report_el_torito`.

### 3.4 Hardware — pending you

- Dell Inspiron 3558 not accessible from this sandbox. After cloud ISO, follow §1.4 and record kernel, GRUB entry, desktop screenshot, and any `jarvis-recovery --check` output.

**Current honest summary:**

| Level | Status | Evidence |
|-------|--------|----------|
| 1 Build | ✅ dry-run, ⏳ real build | `build.sh --dry-run` logs above, workflow ready |
| 2 ISO structure | ⏳ pending ISO | `validate-iso.sh` exists, will run on artifact |
| 3 VM boot | ⏳ pending ISO | `qemu-test.sh` + CI `smoke-boot` ready |
| 4 Laptop | ⏳ pending you | procedure in §1.4 |
| 5 Install | ❌ not yet | live-only, no installer |

---

## 4. How to report a new test run

After each build, append to `docs/STATUS.md` “Last build result” (commit, time, size, sha256, validator output, QEMU head, hardware notes) and update the table above. Keep the last 3 runs; truncate older.

---

## 5. Troubleshooting test failures

See `docs/BUILDING.md §4` table. For validator failures:

- **“ISO too small”** → `ls -lh build/work/iso/live/` and `build/work/xorriso.log`.
- **“UEFI missing”** → `xorriso -indev out/JARVISOS.iso -ls /EFI/BOOT --` and `build/work/xorriso.log` (grub-mkstandalone).
- **“sha256 mismatch”** → re-download artifact, `sha256sum` vs `.sha256`, and check artifact retention (14 days).
- **“JARVIS shows GTK error”** → `jarvis-center --cli` and `dpkg -l | grep gir1.2-gtk`.

Never edit a test to pass — fix the ISO and rebuild.
