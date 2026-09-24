"""Local clap detection: onset energy + spectral shape, adaptive noise floor,
sequence counting (single/double/triple) with timing window and cooldown.

Detection logic operates on float sample blocks so it is fully unit-testable;
`MicListener` feeds it from the microphone via `sounddevice` when available.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

SR = 16000
BLOCK = 512   # 32 ms


def rms(block: Sequence[float]) -> float:
    if np is not None:
        a = np.asarray(block, dtype=np.float32)
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    return math.sqrt(sum(x * x for x in block) / max(len(block), 1))


def high_freq_ratio(block: Sequence[float], sr: int = SR) -> float:
    """Fraction of energy above 1.5 kHz. Claps are broadband/bright; voice & thumps are not."""
    if np is None:
        # zero-crossing-rate proxy
        zc = sum(1 for a, b in zip(block, block[1:]) if (a >= 0) != (b >= 0))
        return min(1.0, zc / max(len(block), 1) * 4)
    a = np.asarray(block, dtype=np.float32)
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    freqs = np.fft.rfftfreq(len(a), 1 / sr)
    tot = spec.sum() + 1e-12
    return float(spec[freqs > 1500].sum() / tot)


@dataclass
class ClapEvent:
    count: int
    ts: float
    confidence: float


class ClapDetector:
    def __init__(self, sensitivity: float = 0.5, window_ms: int = 600, cooldown_ms: int = 1500,
                 min_gap_ms: int = 120, sr: int = SR, max_count: int = 3):
        self.sr = sr
        self.set_params(sensitivity, window_ms, cooldown_ms, min_gap_ms)
        self.max_count = max_count
        self.noise = 0.005        # adaptive noise floor (RMS)
        self.prev_rms = 0.0
        self.claps: list[float] = []
        self.last_emit = -1e9
        self.last_onset = -1e9

    def set_params(self, sensitivity=None, window_ms=None, cooldown_ms=None, min_gap_ms=None):
        if sensitivity is not None:
            self.sensitivity = min(1.0, max(0.0, sensitivity))
            # higher sensitivity → lower ratio over noise floor required
            self.ratio = 12.0 - 9.0 * self.sensitivity     # 3x … 12x the noise floor
            self.min_abs = 0.08 - 0.06 * self.sensitivity  # absolute floor
        if window_ms is not None:
            self.window = window_ms / 1000
        if cooldown_ms is not None:
            self.cooldown = cooldown_ms / 1000
        if min_gap_ms is not None:
            self.min_gap = min_gap_ms / 1000

    def calibrate(self, blocks: list[Sequence[float]]) -> float:
        vals = sorted(rms(b) for b in blocks)
        if vals:
            self.noise = max(vals[int(len(vals) * 0.9) - 1 if len(vals) > 1 else 0], 1e-4)
        return self.noise

    def is_onset(self, block: Sequence[float], t: float) -> tuple[bool, float]:
        r = rms(block)
        onset = False
        conf = 0.0
        sharp = r > self.prev_rms * 2.5    # sudden rise, not sustained noise
        if r > max(self.noise * self.ratio, self.min_abs) and sharp and t - self.last_onset >= self.min_gap:
            hf = high_freq_ratio(block, self.sr)
            if hf > 0.35:
                onset = True
                conf = min(1.0, 0.5 * hf + 0.5 * min(1.0, r / (self.noise * self.ratio * 3)))
        if not onset:  # adapt noise floor slowly to non-clap audio
            self.noise = 0.98 * self.noise + 0.02 * min(r, self.noise * 3) if r > 0 else self.noise
            self.noise = max(self.noise, 1e-4)
        self.prev_rms = r
        return onset, conf

    def process(self, block: Sequence[float], t: float) -> ClapEvent | None:
        """Feed one block with its timestamp (seconds). Returns an event when a sequence finishes."""
        ev = None
        if self.claps and t - self.claps[-1] > self.window:
            ev = self._emit(t)
        onset, conf = self.is_onset(block, t)
        if onset:
            self.last_onset = t
            if t - self.last_emit >= self.cooldown:
                self.claps.append(t)
                self._conf = conf
                if len(self.claps) >= self.max_count:
                    ev = self._emit(t)
        return ev

    def flush(self, t: float) -> ClapEvent | None:
        if self.claps and t - self.claps[-1] > self.window:
            return self._emit(t)
        return None

    def _emit(self, t: float) -> ClapEvent:
        ev = ClapEvent(len(self.claps), t, getattr(self, "_conf", 0.7))
        self.claps = []
        self.last_emit = t
        return ev


class MicListener(threading.Thread):
    def __init__(self, detector: ClapDetector, on_event: Callable[[ClapEvent], None],
                 device: str | None = None, on_status: Callable[[str], None] = print,
                 extra_consumers: list[Callable] | None = None):
        super().__init__(daemon=True, name="clap-listener")
        self.det, self.on_event, self.device, self.on_status = detector, on_event, device, on_status
        self._stop = threading.Event()
        self.status = "stopped"
        self.consumers = extra_consumers or []

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            self.status = f"error: sounddevice/PortAudio unavailable ({e}) — sudo apt install python3-sounddevice libportaudio2"
            return self.on_status(self.status)
        try:
            dev = None if self.device in (None, "", "default") else self.device
            stream = sd.InputStream(samplerate=SR, blocksize=BLOCK, channels=1, dtype="float32", device=dev)
            stream.start()
        except Exception as e:
            self.status = f"unavailable: no microphone ({e}) — clap & voice disabled"
            return self.on_status(self.status)
        self.status = "running"
        self.on_status(self.status)
        # calibrate for 1 s
        calib = [stream.read(BLOCK)[0][:, 0] for _ in range(SR // BLOCK)]
        self.det.calibrate(calib)
        try:
            while not self._stop.is_set():
                data, _ = stream.read(BLOCK)
                block = data[:, 0]
                t = time.monotonic()
                ev = self.det.process(block, t)
                if ev:
                    self.on_event(ev)
                for c in self.consumers:
                    c(block)
        finally:
            stream.stop()
            stream.close()
            self.status = "stopped"
