# Performance Guide

**Design budget for a 4 GB laptop:** Openbox + tint2 + LightDM take about 150 MB. jarvisd is idle and
event-driven, and systemd caps it with `MemoryHigh=450M` / `MemoryMax=700M`. The Qt HUD takes roughly
60–90 MB. No local LLM is shipped. Cloud providers are the default, and Ollama/llama.cpp are optional.
*These numbers are targets. They haven't been measured on real hardware yet (see README status).*

- **First run on a machine with 4 GB or less** switches to Performance Mode automatically: HUD at 15 fps,
  no particles, camera at 320×240 and 8 fps.
- **Resource governor:** every 3 s it computes a load factor from CPU and available RAM. Above 0.8 it
  switches Cinematic → Performance, and camera FPS scales down with load.
- **Screenshots** happen only when needed. They are downscaled (`vision.screenshot_scale`) and include
  OCR to cut vision-model tokens. There is one model call per action *batch*, never one per mouse move.
- **Sensors** run only when enabled. MediaPipe uses `model_complexity=0`.
- The HUD doesn't animate while hidden, and reduced-motion drops it to 2 fps.

# Troubleshooting

| Symptom | Fix |
|---|---|
| HUD says CORE OFFLINE | `systemctl --user restart jarvisd`; `journalctl --user -u jarvisd` |
| "no AI provider" | `jarvis provider add gemini --key` (offline commands still work) |
| Hand tracking: MediaPipe not installed | `pip install --user mediapipe` or rebuild with `JARVIS_WITH_MEDIAPIPE=1` |
| no camera detected | Check `ls /dev/video*`. The rest of JARVIS keeps running |
| Clap: PortAudio unavailable / no microphone | `sudo apt install libportaudio2 python3-sounddevice`; check `pavucontrol` |
| Too many false claps | "reduce false clap detections" or lower `audio.clap_sensitivity` |
| Voice: no Vosk model | Rebuild with `JARVIS_WITH_VOSK=1`, or unzip vosk-model-small-en-us into `~/.local/share/jarvis/vosk-model` |
| GUI actions do nothing | `jarvis doctor`. You need DISPLAY, pyautogui, xdotool and wmctrl |
| Broken config | `jarvis config restore` (latest backup) or `jarvis config reset` |
| Everything is weird | Right-click desktop → STOP JARVIS; plain Openbox + terminal remain usable |
| Build: "Missing dependencies" | `./install-dependencies.sh` |
| Build fails fetching packages | Check mirror access; set `JARVIS_MIRROR=http://<mirror>/debian` |
| test-iso.sh times out without KVM | TCG is slow. Set `JARVIS_BOOT_TIMEOUT=1500` |

# Known limitations
- The ISO has not been built or booted yet: the Debian mirrors were unreachable from the development sandbox.
- Unreal Engine is proprietary, around 100 GB, and needs a strong GPU. It isn't practical on the 4 GB
  target. JARVIS supports it only where it's already installed.
- Wayland isn't supported. PyAutoGUI and xdotool require Xorg, which is what JARVISOS ships.
- Proprietary apps (VS Code, Brave, Discord) aren't redistributed. JARVIS shows the official install method.
- Gesture and clap detectors are tested on synthetic data. Real-world thresholds may need calibration
  through Settings.
