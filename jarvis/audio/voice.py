"""Voice input: offline Vosk by default, cloud STT only if explicitly allowed.
Push-to-talk records a bounded clip; wake-phrase mode runs Vosk locally only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

SR = 16000
VOSK_MODEL_DIRS = [Path("/usr/share/jarvis/vosk-model"), Path.home() / ".local/share/jarvis/vosk-model"]


class VoiceUnavailable(RuntimeError):
    pass


def find_vosk_model() -> Path | None:
    for d in VOSK_MODEL_DIRS:
        if d.is_dir():
            return d
    return None


def record(seconds: float = 5.0, device=None):
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:
        raise VoiceUnavailable(f"no audio stack: {e}")
    try:
        data = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="int16", device=device)
        sd.wait()
    except Exception as e:
        raise VoiceUnavailable(f"no microphone: {e}")
    return data[:, 0].tobytes()


def transcribe_vosk(pcm: bytes) -> str:
    try:
        from vosk import Model, KaldiRecognizer
    except ImportError:
        raise VoiceUnavailable("vosk not installed (pip install --user vosk) — see docs/VOICE.md")
    m = find_vosk_model()
    if not m:
        raise VoiceUnavailable("no Vosk model; run `jarvis-setup voice` to download vosk-model-small-en-us (40 MB)")
    rec = KaldiRecognizer(Model(str(m)), SR)
    rec.AcceptWaveform(pcm)
    return json.loads(rec.FinalResult()).get("text", "")


def speak(text: str) -> None:
    """Voice feedback via espeak-ng (tiny, offline)."""
    for exe in ("espeak-ng", "espeak"):
        if shutil.which(exe):
            subprocess.Popen([exe, "-s", "165", text[:400]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def listen_once(cfg_get: Callable[[str], object], seconds: float = 5.0) -> str:
    if not cfg_get("audio.voice"):
        raise VoiceUnavailable("voice is disabled (say or type: enable voice)")
    provider = cfg_get("audio.voice_provider")
    dev = cfg_get("audio.microphone")
    pcm = record(seconds, None if dev in ("", "default") else dev)
    if provider == "vosk":
        return transcribe_vosk(pcm)
    if provider == "cloud":
        if not cfg_get("audio.cloud_stt_allowed"):
            raise VoiceUnavailable("cloud speech recognition not allowed; enable audio.cloud_stt_allowed first")
        raise VoiceUnavailable("cloud STT adapter not configured")
    raise VoiceUnavailable(f"voice provider {provider} not available")


def strip_wake(text: str, wake: str = "jarvis") -> str | None:
    t = text.strip().lower()
    if t.startswith(wake):
        return t[len(wake):].lstrip(" ,.")
    return None
