# JARVISOS User Guide

## Booting
- **VM:** `./run-qemu.sh out/jarvisos-*.iso` (add `--uefi` to test UEFI).
- **USB:** write the ISO with a tool you trust (e.g. GNOME Disks "Restore Disk Image", or
  `dd` *only after triple-checking the target device*). The JARVIS build scripts never write to disks.
- **Persistence:** create an ext4 partition labelled `persistence` containing a file `persistence.conf`
  with the single line `/ union`. Settings in `~/.config/jarvis` then survive reboots.
- The live user is `jarvis` and logs in automatically.

## Desktop
The HUD is the desktop layer. Normal windows always sit on top of it, so it never blocks apps.

| Shortcut | Action |
|---|---|
| Super+Space | Show HUD / command input |
| Super+V | Voice command (push-to-talk) |
| Super+A | Application launcher (all installed `.desktop` apps) |
| Super+F | JARVIS Files |
| Super+Enter | Terminal |
| Ctrl+Super+Esc / Esc in HUD / STOP button | **Emergency stop** |
| Super+←/→ | Switch workspace |

Right-click the desktop for the Openbox menu. It includes *Restart JARVIS core* and recovery tools.
The `—` button hides the HUD. The `◐` button toggles Performance/Cinematic mode.

## Talking to JARVIS
Type in the HUD, run `jarvis do "…"`, open `jarvis repl`, or use your voice. Examples that work
**offline, without any AI provider**:

```
switch to performance mode            enable cinematic mode
make the HUD brighter                 turn off the orbital rings
set camera FPS to 10                  move the system monitor to the left
when I clap twice, open VS Code       when I show two fingers, open Blender
use pinch as left click               disable thumbs-down detection
increase clap sensitivity             change the double-clap timing window to 800ms
switch to assisted mode               open firefox
run `df -h`                           create folder Projects/game
stop
```

Multi-step goals ("set up a Python project with tests and open it in VS Code") use the configured
AI provider. Add one under Settings → AI Providers, or run `jarvis provider add gemini --key`.

## Control modes
| Mode | Behaviour |
|---|---|
| SAFE (default) | Asks before anything that changes state |
| ASSISTED | Low-risk actions run automatically. It asks before deleting, sending, or making system/root changes |
| AUTONOMOUS | Runs multi-step tasks automatically. It asks before anything irreversible, sending, or system changes |
| FULL | Broad control within the permissions you granted. It still confirms anything irreversible or critical |

Raising the mode needs approval. Lowering it never does. **Disk, EFI, bootloader, authentication
and security-protection changes always ask, in every mode.**

## Gestures and claps
Turn them on in Settings → Vision & Gestures / Audio, or say "enable hand tracking" or "enable clap detection".
Hand tracking needs a camera and MediaPipe (build with `JARVIS_WITH_MEDIAPIPE=1`, or run
`pip install --user mediapipe`). Clap detection needs a microphone. If either is missing, JARVIS
explains what is needed and everything else keeps working.

## Files and editor
JARVIS Files supports tabs, favorites, filter/search, hidden files (Ctrl+H), preview, properties,
copy/cut/paste, rename (F2), and delete to trash with **Ctrl+Z undo**. Text files open in JARVIS Editor,
which has line numbers, highlighting, find/replace, and unsaved-change protection. Root-owned files
ask for approval and a password, and are backed up to `~/.config/jarvis/file-backups/` first.

## Privacy controls
- `jarvis audit` shows the log. It supports `--search`, `--export`, and `--clear`.
- `jarvis memory show|clear|export|import` manages stored memory.
- `jarvis permissions [name on|off]` views or changes permissions.
- The microphone is never streamed to the cloud unless you enable `audio.cloud_stt_allowed`.
