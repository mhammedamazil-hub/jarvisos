# How to Get Your First JARVISOS ISO — Beginner Steps

**You are on Linux Mint, your laptop has 4 GB RAM — we build the ISO in the cloud so your laptop stays free.**

This uses your actual repository: `mhammedamazil-hub/jarvisos`, branch `arena/5a9160ac-jarvisos`, workflow `.github/workflows/build-jarvisos.yml`, output `out/JARVISOS.iso` + `out/JARVISOS.iso.sha256`.

---

## Step 1 — Push your code to GitHub (if not yet pushed)

On your Linux Mint terminal:

```bash
cd ~/jarvisos   # or wherever you cloned
git status      # should be on arena/5a9160ac-jarvisos, clean
git push origin arena/5a9160ac-jarvisos
```

If `git push` asks for login, use `gh auth login` or GitHub Desktop. You need to be the owner `mhammedamazil-hub`.

---

## Step 2 — Open the cloud builder in your browser

1. Go to **https://github.com/mhammedamazil-hub/jarvisos**
2. Click **Actions** at the top (between Code and Issues)
3. On the left, click **Build JARVISOS**

You should see a list of previous runs (maybe empty). On the right, a grey button **Run workflow**.

---

## Step 3 — Start the build

1. Click **Run workflow** → a small box opens
2. **Use workflow from:** choose `arena/5a9160ac-jarvisos` (not main)
3. **Fast build:** leave `false` (recommended; `true` is faster but makes a bigger ISO)
4. Click the green **Run workflow** button

A new row appears with a yellow dot ● — it is running.

---

## Step 4 — Watch the progress (no need to stare)

Click the row that appeared.

You will see 3 jobs:

- **Static checks** — should turn green ✓ in ~1 minute
- **Build ISO** — takes **35–60 minutes** — click it to see logs scrolling:
  - `Installing 100 packages...`
  - `mksquashfs ...`
  - `Building GRUB EFI standalone...`
  - `ISO size: 1356 MB` and `✓ checksum verified`
- **QEMU smoke boot test** — ~3 minutes, checks the ISO can boot in a virtual machine

You can close the tab; GitHub will email you when it finishes.

**If it turns red ✗:** click **Build ISO**, scroll to the bottom red lines, and see `docs/BUILDING.md §4` for what each error means. Do not click “Re-run” 3 times without reading — the log tells you if it was a network hiccup (retry is ok) or a packaging error (needs a code fix).

---

## Step 5 — Download the finished ISO

When the run is **green ✓**:

1. Scroll to the bottom of that run’s page
2. Find **Artifacts** section
3. Click **JARVISOS-iso** — your browser downloads `JARVISOS-iso.zip`
4. Unzip it on your Linux Mint:

```bash
cd ~/Downloads
unzip JARVISOS-iso.zip -d jarvisos-artifact
ls -lh jarvisos-artifact
# you should see:
#  JARVISOS.iso      (about 1.1–1.6 GB)
#  JARVISOS.iso.sha256   (a small text file with the checksum)
#  JARVISOS.iso.md5
#  build.log         (full log)
#  installed-packages.txt
```

Artifacts are kept **14 days** — download soon.

---

## Step 6 — Verify the ISO is not corrupted (mandatory)

In the same folder as the ISO, run:

```bash
cd ~/Downloads/jarvisos-artifact
sha256sum -c JARVISOS.iso.sha256
```

Must print:

```
JARVISOS.iso: OK
```

If it says **FAILED**, delete and re-download the ZIP. **Never flash a FAILED ISO to USB.**

You can also double-check structure (needs `xorriso`):

```bash
sudo apt-get install xorriso   # if not installed
../../jarvisos/scripts/validate-iso.sh JARVISOS.iso
# must print:
# ✓ size plausible
# ✓ UEFI BOOTX64.EFI present
# ✓ live filesystem.squashfs present
# ✓ kernel present
```

---

## Step 7 — Test safely in a virtual machine (optional, no risk)

This does **not** touch your Mint install.

```bash
sudo apt-get install qemu-system-x86 ovmf xorriso
./scripts/qemu-test.sh ~/Downloads/jarvisos-artifact/JARVISOS.iso
# Both BIOS and UEFI attempts will “time out” after 20 seconds — that is expected.
# Look for: xorriso shows “El Torito” and no “No bootable device” error.
```

For a graphical QEMU window (if you have KVM):

```bash
qemu-system-x86_64 -enable-kvm -m 2048 -cdrom ~/Downloads/jarvisos-artifact/JARVISOS.iso -boot d
# or UEFI:
qemu-system-x86_64 -enable-kvm -m 2048 -bios /usr/share/OVMF/OVMF.fd -cdrom ~/Downloads/jarvisos-artifact/JARVISOS.iso -boot d
```

You should see the GRUB menu “JARVISOS Live”. If you do, level 3 (VM boot) passes.

---

## Step 8 — Test on your real Dell 3558 (live, without installing)

> **Warning:** the next command can erase a disk if you pick the wrong device. Triple-check.

**Find your USB stick:**

```bash
lsblk -d -o NAME,SIZE,MODEL,MTRAN
# Example: sda 238G (your internal SSD), sdb 14.9G (your USB) — USE sdb, NOT sda
```

**Flash the ISO (erases the USB):**

```bash
# Replace /dev/sdb with your USB device from above — NOT a number like sdb1
sudo dd if=~/Downloads/jarvisos-artifact/JARVISOS.iso of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

Safer graphical alternative: open **Disks** → select USB → menu → **Restore Disk Image** → pick `JARVISOS.iso`.

**Boot the laptop:**

1. Insert USB, reboot
2. Press **F12** repeatedly → Boot Menu → pick your USB under “UEFI”
   - If you see “Secure Boot violation”, reboot, press **F2** → Secure Boot → **Disabled**, then F12 again
3. GRUB menu → **JARVISOS Live**
4. Wait 30–60 seconds → you should see LightDM → Openbox desktop with dark navy background, tint2 bar at bottom
5. Press **Super+Space** (Windows key + Space) → **JARVIS Command Center** appears centered
   - Type `help` → should list commands
   - Type `open terminal` → terminal opens
   - Type `system info` → shows memory/load
   - Click **Launcher** tab → all apps appear
6. If desktop is black: press **Ctrl+Alt+F2**, login `jarvis` / `jarvis`, run `jarvis-recovery --check`

**Login:** live user `jarvis` password `jarvis` (`sudo` works without password in live).

**Important:** this is a **live** system — it does not install unless you later run an installer. Reboot without USB returns to Mint.

---

## Step 9 — What to do after it boots

- **Network:** click the network icon in tint2 → connect WiFi
- **JARVIS AI (optional):** open JARVIS → **Settings** → choose provider (`openai`, `anthropic`, `ollama`, `custom`), paste your API key, set model, click **Save** → key is saved to `~/.config/jarvis/config.json` with `chmod 600` and never leaves your laptop
- **Staying offline:** leave provider `none` — JARVIS still works for launching apps offline

**Never paste your API key in chat, email, or Git — Settings is the only place.**

---

## Step 10 — Report back or rebuild

- If something failed, copy the relevant lines from `build.log` or `/var/log/Xorg.0.log` and open an issue or update `docs/STATUS.md`.
- To make changes and rebuild: edit files, `git commit -m "..."`, `git push`, then **Actions → Run workflow** again — each build records its own SHA256 so you can compare.

---

## Quick reference — actual paths in this repo

- Workflow file: `.github/workflows/build-jarvisos.yml`
- Build script: `build/build.sh` (real) and `build/build.sh --dry-run` (check)
- Package lists: `config/package-lists/*.list.chroot` (100 packages)
- Overlay (your desktop): `overlay/usr/local/bin/jarvis-center`, `overlay/etc/skel/.config/openbox/*`, `overlay/etc/skel/.config/tint2/tint2rc`
- Scripts: `scripts/validate-iso.sh`, `scripts/test-jarvis.sh`, `scripts/qemu-test.sh`
- Output after build: `out/JARVISOS.iso`, `out/JARVISOS.iso.sha256`
- Artifact on GitHub: **Actions → Build JARVISOS → Artifacts → JARVISOS-iso (14 days)**

If a path in this guide does not exist on your machine, you are in the wrong folder — `cd` to your `jarvisos` checkout first.

---

## One-line version for the impatient

```bash
git push origin arena/5a9160ac-jarvisos   # then on GitHub: Actions → Build JARVISOS → Run workflow → wait green → download JARVISOS-iso.zip → unzip → sha256sum -c JARVISOS.iso.sha256 → flash to USB with dd or Disks → F12 → JARVISOS Live
```
