"""Local hand-gesture recognition from 21 MediaPipe-style landmarks.

Pipeline: camera -> landmarks -> classify_static / SwipeDetector -> GestureEngine
(confirmation over N frames + per-gesture cooldown) -> GestureEvent -> action router.
Detection, confirmation and execution are separate stages so a single noisy frame
never fires an action.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

Point = tuple[float, float, float]
WRIST, THUMB_TIP, THUMB_IP, THUMB_MCP = 0, 4, 3, 2
TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
PIPS = {"index": 6, "middle": 10, "ring": 14, "pinky": 18}
MCPS = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}


def _d(a: Point, b: Point) -> float:
    return math.dist(a[:2], b[:2])


def hand_scale(lm: Sequence[Point]) -> float:
    return max(_d(lm[WRIST], lm[MCPS["middle"]]), 1e-6)


def finger_states(lm: Sequence[Point]) -> dict[str, bool]:
    """Extended fingers, orientation-independent (distance from wrist tip vs pip)."""
    s = {}
    for f in TIPS:
        s[f] = _d(lm[TIPS[f]], lm[WRIST]) > _d(lm[PIPS[f]], lm[WRIST]) * 1.1
    # thumb: tip far from index MCP relative to hand size
    s["thumb"] = _d(lm[THUMB_TIP], lm[MCPS["index"]]) > 0.55 * hand_scale(lm) and \
        _d(lm[THUMB_TIP], lm[WRIST]) > _d(lm[THUMB_IP], lm[WRIST])
    return s


def classify_static(lm: Sequence[Point], pinch_threshold: float = 0.05) -> tuple[str | None, float]:
    """Return (gesture, confidence) for one frame."""
    if not lm or len(lm) != 21:
        return None, 0.0
    sc = hand_scale(lm)
    s = finger_states(lm)
    fingers = [s["index"], s["middle"], s["ring"], s["pinky"]]
    n = sum(fingers)
    pinch_d = _d(lm[THUMB_TIP], lm[TIPS["index"]]) / sc
    # thumb & index tips touching (threshold expressed per 0.2 hand-lengths)
    if pinch_d < pinch_threshold * 5 and (s["middle"] or s["ring"] or s["pinky"]):
        return "PINCH", max(0.5, min(1.0, 1 - pinch_d))
    if n == 0 and s["thumb"]:
        up = lm[THUMB_TIP][1] < lm[WRIST][1] - 0.3 * sc   # image y grows downward
        down = lm[THUMB_TIP][1] > lm[WRIST][1] + 0.3 * sc
        if up:
            return "THUMBS_UP", 0.9
        if down:
            return "THUMBS_DOWN", 0.9
    if n == 0 and not s["thumb"]:
        return "FIST", 0.9
    if n == 4 and s["thumb"]:
        return "OPEN_PALM", 0.92
    if n == 4:
        return "FOUR_FINGERS", 0.85
    if fingers == [True, False, False, False]:
        return ("POINTING" if not s["thumb"] else "ONE_FINGER"), 0.85
    if fingers == [True, True, False, False]:
        return "TWO_FINGERS", 0.88
    if fingers == [True, True, True, False]:
        return "THREE_FINGERS", 0.85
    return None, 0.0


class SwipeDetector:
    def __init__(self, window_s: float = 0.5, min_dist: float = 0.25):
        self.window_s, self.min_dist = window_s, min_dist
        self.hist: deque = deque()

    def update(self, wrist: tuple[float, float], t: float | None = None) -> str | None:
        t = time.monotonic() if t is None else t
        self.hist.append((t, wrist))
        while self.hist and t - self.hist[0][0] > self.window_s:
            self.hist.popleft()
        if len(self.hist) < 3:
            return None
        (_, (x0, y0)), (_, (x1, y1)) = self.hist[0], self.hist[-1]
        dx, dy = x1 - x0, y1 - y0
        if max(abs(dx), abs(dy)) < self.min_dist:
            return None
        self.hist.clear()
        if abs(dx) > abs(dy) * 1.5:
            return "SWIPE_RIGHT" if dx > 0 else "SWIPE_LEFT"   # (camera image is mirrored upstream)
        if abs(dy) > abs(dx) * 1.5:
            return "SWIPE_DOWN" if dy > 0 else "SWIPE_UP"
        return None


# ------------------------------------------------------------ custom gestures
def normalize(lm: Sequence[Point]) -> list[float]:
    """Translation/scale-invariant feature vector."""
    wx, wy = lm[WRIST][0], lm[WRIST][1]
    sc = hand_scale(lm)
    return [c for p in lm for c in ((p[0] - wx) / sc, (p[1] - wy) / sc)]


def train_custom(samples: list[Sequence[Point]]) -> dict:
    if len(samples) < 3:
        raise ValueError("need at least 3 samples")
    vecs = [normalize(s) for s in samples]
    centroid = [sum(c) / len(vecs) for c in zip(*vecs)]
    spread = max(math.dist(v, centroid) for v in vecs)
    return {"centroid": centroid, "radius": max(spread * 1.5, 0.5), "samples": len(samples)}


def match_custom(lm: Sequence[Point], customs: dict[str, dict]) -> tuple[str | None, float]:
    if not customs:
        return None, 0.0
    v = normalize(lm)
    best, bd = None, 1e9
    for name, g in customs.items():
        d = math.dist(v, g["centroid"])
        if d < g["radius"] and d < bd:
            best, bd = name, d
    if not best:
        return None, 0.0
    return f"CUSTOM:{best}", max(0.5, 1 - bd / customs[best]["radius"] / 2)


# ------------------------------------------------------------ engine
@dataclass
class GestureEvent:
    gesture: str
    confidence: float
    ts: float


class GestureEngine:
    """Confirmation + cooldown. Feed it one frame at a time."""

    def __init__(self, mappings_getter: Callable[[], list[dict]], confirm_frames: int = 4,
                 default_cooldown_ms: int = 1000, pinch_threshold: float = 0.05,
                 customs_getter: Callable[[], dict] = lambda: {}):
        self.mappings_getter = mappings_getter
        self.confirm_frames = confirm_frames
        self.default_cooldown_ms = default_cooldown_ms
        self.pinch_threshold = pinch_threshold
        self.customs_getter = customs_getter
        self.swipe = SwipeDetector()
        self._candidate: str | None = None
        self._count = 0
        self._last_fire: dict[str, float] = {}
        self._held: str | None = None   # prevents repeat while gesture held

    def mapping(self, g: str) -> dict | None:
        return next((m for m in self.mappings_getter() if m["gesture"] == g), None)

    def process(self, lm: Sequence[Point] | None, t: float | None = None) -> GestureEvent | None:
        t = time.monotonic() if t is None else t
        if not lm:
            self._candidate, self._count, self._held = None, 0, None
            self.swipe.hist.clear()
            return None
        sw = self.swipe.update((lm[WRIST][0], lm[WRIST][1]), t)
        if sw:
            return self._fire(sw, 0.8, t)
        g, conf = match_custom(lm, self.customs_getter())
        if not g:
            g, conf = classify_static(lm, self.pinch_threshold)
        if g is None:
            self._candidate, self._count, self._held = None, 0, None
            return None
        if g == self._held:
            return None
        if g == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = g, 1
        if self._count >= self.confirm_frames:
            ev = self._fire(g, conf, t)
            if ev:
                self._held = g
            return ev
        return None

    def _fire(self, g: str, conf: float, t: float) -> GestureEvent | None:
        m = self.mapping(g)
        if m is not None:
            if not m.get("enabled", True) or conf < m.get("confidence_threshold", 0.75):
                return None
            cd = m.get("cooldown_ms", self.default_cooldown_ms)
        else:
            cd = self.default_cooldown_ms
        if (t - self._last_fire.get(g, -1e9)) * 1000 < cd:
            return None
        self._last_fire[g] = t
        return GestureEvent(g, conf, t)


class HandTracker(threading.Thread):
    """Camera loop. Degrades gracefully when camera / OpenCV / MediaPipe are missing."""

    def __init__(self, engine: GestureEngine, on_event: Callable[[GestureEvent], None],
                 cfg_get: Callable[[str], object], on_status: Callable[[str], None] = print,
                 load_factor: Callable[[], float] = lambda: 0.0):
        super().__init__(daemon=True, name="hand-tracker")
        self.engine, self.on_event, self.cfg_get, self.on_status = engine, on_event, cfg_get, on_status
        self.load_factor = load_factor
        self._stop = threading.Event()
        self.status = "stopped"
        self.last_landmarks = None

    def stop(self):
        self._stop.set()

    def _set(self, s):
        self.status = s
        self.on_status(s)

    def run(self):
        try:
            import cv2
        except ImportError:
            return self._set("error: OpenCV not installed — sudo apt install python3-opencv")
        try:
            import mediapipe as mp
        except ImportError:
            return self._set("error: MediaPipe not installed — pip install --user mediapipe (hand tracking disabled)")
        cap = cv2.VideoCapture(int(self.cfg_get("vision.camera_index")))
        if not cap.isOpened():
            return self._set("unavailable: no camera detected — hand tracking disabled")
        w, h = map(int, str(self.cfg_get("vision.resolution")).split("x"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                         min_detection_confidence=float(self.cfg_get("vision.detection_confidence")),
                                         min_tracking_confidence=float(self.cfg_get("vision.tracking_confidence")))
        self._set("running")
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    self._set("error: camera read failed")
                    break
                frame = cv2.flip(frame, 1)
                res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                lm = None
                if res.multi_hand_landmarks:
                    lm = [(p.x, p.y, p.z) for p in res.multi_hand_landmarks[0].landmark]
                self.last_landmarks = lm
                ev = self.engine.process(lm)
                if ev:
                    self.on_event(ev)
                fps = max(1, int(self.cfg_get("vision.fps")))
                fps = max(1, int(fps * (1 - 0.6 * self.load_factor())))   # auto-throttle under load
                self._stop.wait(max(0, 1 / fps - (time.monotonic() - t0)))
        finally:
            cap.release()
            hands.close()
            if self.status == "running":
                self._set("stopped")

    def capture_samples(self, n: int = 8, interval: float = 0.4) -> list:
        """For custom gesture training — reuse the running tracker's landmarks."""
        out = []
        while len(out) < n and not self._stop.is_set():
            if self.last_landmarks:
                out.append(list(self.last_landmarks))
            time.sleep(interval)
        return out
