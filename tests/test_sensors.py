import math

import numpy as np
import pytest

from jarvis.vision.gestures import classify_static, GestureEngine, SwipeDetector, train_custom, match_custom
from jarvis.audio.clap import ClapDetector, SR, BLOCK


# ---------------------------------------------------------------- synthetic hands
def hand(ext: dict, thumb: str = "in", pinch=False, dx=0.0):
    """Build 21 landmarks for an upright hand (wrist at bottom, y grows downward)."""
    lm = [(0.5 + dx, 0.8, 0)] * 21
    lm = list(lm)
    bases = {"index": 0.44, "middle": 0.5, "ring": 0.56, "pinky": 0.62}
    ids = {"index": (5, 6, 7, 8), "middle": (9, 10, 11, 12), "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
    for f, x in bases.items():
        m, p, d, t = ids[f]
        lm[m] = (x + dx, 0.6, 0)
        lm[p] = (x + dx, 0.5, 0)
        if ext.get(f):
            lm[d], lm[t] = (x + dx, 0.42, 0), (x + dx, 0.35, 0)
        else:
            lm[d], lm[t] = (x + dx, 0.58, 0), (x + dx, 0.64, 0)
    if thumb == "out":
        lm[2], lm[3], lm[4] = (0.40 + dx, 0.7, 0), (0.34 + dx, 0.66, 0), (0.28 + dx, 0.62, 0)
    elif thumb == "up":
        lm[2], lm[3], lm[4] = (0.42 + dx, 0.66, 0), (0.40 + dx, 0.5, 0), (0.39 + dx, 0.38, 0)
    elif thumb == "down":
        lm = [(x, 1.6 - y, z) for x, y, z in lm]  # flip vertically
        return lm if False else _thumb_down()
    else:
        lm[2], lm[3], lm[4] = (0.45 + dx, 0.7, 0), (0.46 + dx, 0.64, 0), (0.46 + dx, 0.6, 0)
    if pinch:
        lm[4] = lm[8] = (0.44 + dx, 0.36, 0)
        lm[3] = (0.40 + dx, 0.5, 0)
    return lm


def _thumb_down():
    up = hand({}, "up")
    return [(x, 1.6 - y, z) for x, y, z in up]


@pytest.mark.parametrize("ext,thumb,expected", [
    ({"index": 1, "middle": 1, "ring": 1, "pinky": 1}, "out", "OPEN_PALM"),
    ({}, "in", "FIST"),
    ({"index": 1, "middle": 1}, "in", "TWO_FINGERS"),
    ({"index": 1, "middle": 1, "ring": 1}, "in", "THREE_FINGERS"),
    ({"index": 1}, "in", "POINTING"),
    ({}, "up", "THUMBS_UP"),
])
def test_static_gestures(ext, thumb, expected):
    assert classify_static(hand(ext, thumb))[0] == expected


def test_thumbs_down_and_pinch():
    assert classify_static(_thumb_down())[0] == "THUMBS_DOWN"
    assert classify_static(hand({"middle": 1, "ring": 1, "pinky": 1}, "in", pinch=True))[0] == "PINCH"


def mappings():
    return [{"gesture": "TWO_FINGERS", "action": {"type": "OPEN_APPLICATION", "application": "code"}, "enabled": True,
             "confidence_threshold": 0.75, "cooldown_ms": 1000},
            {"gesture": "FIST", "action": {"type": "UI"}, "enabled": False, "confidence_threshold": 0.5, "cooldown_ms": 0}]


def test_confirmation_and_cooldown():
    e = GestureEngine(mappings, confirm_frames=3)
    two = hand({"index": 1, "middle": 1})
    evs = [e.process(two, t=i * 0.05) for i in range(10)]
    fired = [x for x in evs if x]
    assert len(fired) == 1 and fired[0].gesture == "TWO_FINGERS"   # held gesture fires once
    assert evs[0] is None and evs[1] is None and evs[2] is not None  # needs 3 frames
    e.process(None, t=0.6)                                           # hand removed
    assert all(e.process(two, t=0.6 + i * 0.05) is None for i in range(5))  # still in cooldown (1s)
    e.process(None, t=1.3)
    assert [e.process(two, t=1.3 + i * 0.05) for i in range(3)][-1] is not None


def test_noise_frames_do_not_fire_and_disabled():
    e = GestureEngine(mappings, confirm_frames=3)
    two, fist = hand({"index": 1, "middle": 1}), hand({})
    seq = [two, fist, two, fist, two, fist]
    assert all(e.process(lm, t=i * 0.05) is None for i, lm in enumerate(seq))
    assert all(e.process(fist, t=1 + i * 0.05) is None for i in range(6))  # FIST disabled


def test_swipe():
    s = SwipeDetector()
    out = [s.update((0.2 + i * 0.08, 0.5), t=i * 0.05) for i in range(6)]
    assert "SWIPE_RIGHT" in out
    s2 = SwipeDetector()
    out = [s2.update((0.5, 0.8 - i * 0.08), t=i * 0.05) for i in range(6)]
    assert "SWIPE_UP" in out


def test_custom_gesture():
    rock = hand({"index": 1, "pinky": 1}, "in")
    samples = [[(x + 0.001 * k, y, z) for x, y, z in rock] for k in range(5)]
    g = train_custom(samples)
    assert match_custom(rock, {"rock": g})[0] == "CUSTOM:rock"
    assert match_custom(hand({"index": 1, "middle": 1, "ring": 1, "pinky": 1}, "out"), {"rock": g})[0] is None


# ---------------------------------------------------------------- claps
rng = np.random.default_rng(1)


def noise(level=0.004):
    return rng.normal(0, level, BLOCK).astype(np.float32)


def clap_block():
    b = rng.normal(0, 0.5, BLOCK).astype(np.float32)          # broadband burst
    return b * np.exp(-np.arange(BLOCK) / 80.0).astype(np.float32)


def hum_block(amp=0.6):
    t = np.arange(BLOCK) / SR
    return (amp * np.sin(2 * math.pi * 180 * t)).astype(np.float32)  # loud low-frequency (voice/thump)


def run(det, pattern, dt=BLOCK / SR):
    """pattern: list of 'c' (clap), 'h' (hum), '.' (silence block)."""
    events, t = [], 0.0
    for ch in pattern:
        blk = clap_block() if ch == "c" else hum_block() if ch == "h" else noise()
        e = det.process(blk, t)
        if e:
            events.append(e.count)
        t += dt
    for _ in range(60):
        t += dt
        e = det.process(noise(), t)
        if e:
            events.append(e.count)
    return events


def calibrated(**kw):
    d = ClapDetector(**kw)
    d.calibrate([noise() for _ in range(30)])
    return d


def test_single_double_triple():
    assert run(calibrated(), "....c" + "." * 5) == [1]
    assert run(calibrated(), "....c......c") == [2]            # ~190ms gap
    assert run(calibrated(), "....c......c......c") == [3]


def test_window_splits_sequences():
    # gap of 25 blocks (~800ms) > 600ms window → two single claps (cooldown 0)
    assert run(calibrated(cooldown_ms=0), "c" + "." * 25 + "c") == [1, 1]
    assert run(calibrated(window_ms=1000, cooldown_ms=0), "c" + "." * 25 + "c") == [2]


def test_false_positive_protection():
    assert run(calibrated(), "..hhhhh....hh..") == []          # loud low-freq sound is not a clap
    assert run(calibrated(), "." * 40) == []
    d = calibrated(sensitivity=0.0)
    quiet = lambda: clap_block() * 0.05
    t, evs = 0.0, []
    for i in range(40):
        e = d.process(quiet() if i == 5 else noise(), t)
        t += BLOCK / SR
        if e:
            evs.append(e)
    assert d.flush(t + 2) is None and not evs                  # low sensitivity ignores faint clap


def test_min_gap_debounce():
    # two adjacent blocks of one clap must not count twice
    assert run(calibrated(), "....cc.....") == [1]
