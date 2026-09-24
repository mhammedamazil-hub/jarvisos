"""Structured action batches: schema validation, dry-run, and PyAutoGUI execution.

Batch format:
  {"actions": [{"type": "open_app", "app": "blender"}, {"type": "wait", "seconds": 4},
               {"type": "click", "x": 620, "y": 430}, {"type": "type", "text": "hi"},
               {"type": "press", "key": "enter"}]}
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..core.safety import STOP, StopRequested

ACTION_SPECS: dict[str, dict[str, type | tuple]] = {
    "open_app": {"app": str},
    "wait": {"seconds": (int, float)},
    "move": {"x": (int, float), "y": (int, float)},
    "click": {"x": (int, float), "y": (int, float)},
    "double_click": {"x": (int, float), "y": (int, float)},
    "right_click": {"x": (int, float), "y": (int, float)},
    "drag": {"x": (int, float), "y": (int, float), "to_x": (int, float), "to_y": (int, float)},
    "scroll": {"amount": int},
    "type": {"text": str},
    "press": {"key": str},
    "hotkey": {"keys": list},
    "screenshot": {},
    "wait_window": {"title": str},
    "focus_window": {"title": str},
    "verify_window": {"title": str},
    "verify_text": {"text": str},
}
OPTIONAL = {"button", "clicks", "interval", "duration", "timeout", "window", "retries", "description", "path",
            "region", "x", "y"}
VALID_KEYS = None  # populated lazily from pyautogui.KEYBOARD_KEYS when available
DANGEROUS_HOTKEYS = [{"ctrl", "alt", "delete"}, {"ctrl", "alt", "backspace"}, {"alt", "sysrq"}]
MAX_TEXT = 5000
MAX_WAIT = 120


class ActionError(ValueError):
    pass


def screen_size() -> tuple[int, int]:
    try:
        import pyautogui
        s = pyautogui.size()
        return int(s[0]), int(s[1])
    except Exception:
        w, h = os.environ.get("JARVIS_SCREEN", "1366x768").split("x")
        return int(w), int(h)


def validate_action(a: dict, size: tuple[int, int] | None = None) -> dict:
    if not isinstance(a, dict) or "type" not in a:
        raise ActionError("action must be an object with a 'type'")
    t = a["type"]
    if t not in ACTION_SPECS:
        raise ActionError(f"unknown action type '{t}'")
    for field, typ in ACTION_SPECS[t].items():
        if field not in a:
            if field in ("x", "y") and t in ("click", "double_click", "right_click"):
                continue  # click at current position is allowed
            raise ActionError(f"{t}: missing '{field}'")
        if not isinstance(a[field], typ) or isinstance(a[field], bool):
            raise ActionError(f"{t}: '{field}' has wrong type")
    extra = set(a) - set(ACTION_SPECS[t]) - OPTIONAL - {"type"}
    if extra:
        raise ActionError(f"{t}: unexpected fields {sorted(extra)}")
    w, h = size or screen_size()
    for fx, fy in (("x", "y"), ("to_x", "to_y")):
        if fx in a or fy in a:
            x, y = a.get(fx, 0), a.get(fy, 0)
            if not (0 <= x < w and 0 <= y < h):
                raise ActionError(f"{t}: coordinate ({x},{y}) outside screen {w}x{h}")
    if t == "wait" and not 0 <= a["seconds"] <= MAX_WAIT:
        raise ActionError(f"wait must be 0..{MAX_WAIT}s")
    if t == "type" and len(a["text"]) > MAX_TEXT:
        raise ActionError("text too long")
    if t == "hotkey":
        keys = {k.lower() for k in a["keys"]}
        if any(d <= keys for d in DANGEROUS_HOTKEYS):
            raise ActionError(f"hotkey {a['keys']} is blocked")
        if not all(isinstance(k, str) for k in a["keys"]) or not a["keys"]:
            raise ActionError("hotkey keys must be non-empty strings")
    if t in ("press",) and VALID_KEYS and a["key"].lower() not in VALID_KEYS:
        raise ActionError(f"unknown key {a['key']}")
    if "button" in a and a["button"] not in ("left", "right", "middle"):
        raise ActionError("button must be left/right/middle")
    return a


def validate_batch(batch: dict | list, size: tuple[int, int] | None = None) -> list[dict]:
    actions = batch.get("actions") if isinstance(batch, dict) else batch
    if not isinstance(actions, list) or not actions:
        raise ActionError("batch must contain a non-empty 'actions' list")
    if len(actions) > 100:
        raise ActionError("batch too large (max 100 actions)")
    size = size or screen_size()
    return [validate_action(a, size) for a in actions]


def describe(a: dict) -> str:
    t = a["type"]
    if t in ("click", "double_click", "right_click", "move"):
        return f"{t.replace('_', ' ')} at ({a.get('x', 'cursor')}, {a.get('y', '')})"
    if t == "type":
        txt = a["text"]
        return f"type {len(txt)} chars: {txt[:40]!r}{'…' if len(txt) > 40 else ''}"
    if t == "press":
        return f"press {a['key']}"
    if t == "hotkey":
        return "hotkey " + "+".join(a["keys"])
    if t == "open_app":
        return f"open application {a['app']}"
    if t == "wait":
        return f"wait {a['seconds']}s"
    return t + " " + " ".join(f"{k}={v}" for k, v in a.items() if k != "type")


def dry_run(batch) -> list[str]:
    return [f"{i+1}. {describe(a)}" for i, a in enumerate(validate_batch(batch))]


@dataclass
class StepResult:
    index: int
    action: dict
    ok: bool
    detail: Any = None
    error: str | None = None


class PyAutoGUIController:
    """Executes validated batches. Backend is injectable for testing / headless use."""

    def __init__(self, backend: Any = None, launcher: Callable[[str], dict] | None = None,
                 window_waiter: Callable[[str, float], Any] | None = None,
                 screenshotter: Callable[[], Any] | None = None, pause: float = 0.08,
                 ocr: Callable[[Any], str] | None = None):
        self._backend = backend
        self.launcher = launcher
        self.window_waiter = window_waiter
        self.screenshotter = screenshotter
        self.ocr = ocr
        self.pause = pause
        self._esc_listener = None

    @property
    def gui(self):
        if self._backend is None:
            try:
                import pyautogui
            except Exception as e:  # no display / not installed
                raise ActionError(f"PyAutoGUI unavailable: {e}")
            pyautogui.FAILSAFE = True   # slam mouse into a corner to abort
            pyautogui.PAUSE = self.pause
            global VALID_KEYS
            VALID_KEYS = set(pyautogui.KEYBOARD_KEYS)
            self._backend = pyautogui
        return self._backend

    def start_esc_watch(self) -> None:
        """Global ESC → emergency stop (pynput if available; xbindkeys binding is the fallback)."""
        if self._esc_listener:
            return
        try:
            from pynput import keyboard
        except Exception:
            return
        def on_press(key):
            if key == keyboard.Key.esc:
                STOP.trigger("ESC pressed")
        self._esc_listener = keyboard.Listener(on_press=on_press, daemon=True)
        self._esc_listener.start()

    def execute(self, batch, retries: int = 1, timeout: float = 120,
                on_step: Callable[[StepResult], None] | None = None) -> list[StepResult]:
        actions = validate_batch(batch)
        self.start_esc_watch()
        results: list[StepResult] = []
        deadline = time.time() + timeout
        for i, a in enumerate(actions):
            attempt = 0
            while True:
                try:
                    STOP.check()
                    if time.time() > deadline:
                        raise TimeoutError(f"batch timeout ({timeout}s)")
                    detail = self._do(a)
                    r = StepResult(i, a, True, detail)
                    break
                except StopRequested:
                    results.append(StepResult(i, a, False, error="stopped by user"))
                    raise
                except Exception as e:
                    attempt += 1
                    if attempt > min(a.get("retries", retries), 5):
                        r = StepResult(i, a, False, error=str(e))
                        break
                    STOP.wait(0.5 * attempt)
            results.append(r)
            if on_step:
                on_step(r)
            if not r.ok:
                break
        return results

    def _do(self, a: dict):
        t, g = a["type"], None
        if t in ("move", "click", "double_click", "right_click", "drag", "scroll", "type", "press", "hotkey"):
            g = self.gui
        dur = a.get("duration", 0.15)
        if t == "open_app":
            if not self.launcher:
                raise ActionError("no launcher configured")
            r = self.launcher(a["app"])
            if not r.get("ok"):
                raise ActionError(r.get("error", "launch failed"))
            return r
        if t == "wait":
            STOP.wait(a["seconds"])
            return None
        if t == "move":
            g.moveTo(a["x"], a["y"], duration=dur)
        elif t in ("click", "double_click", "right_click"):
            kw = {"x": a["x"], "y": a["y"]} if "x" in a else {}
            if t == "click":
                g.click(button=a.get("button", "left"), clicks=a.get("clicks", 1), **kw)
            elif t == "double_click":
                g.doubleClick(**kw)
            else:
                g.rightClick(**kw)
        elif t == "drag":
            g.moveTo(a["x"], a["y"])
            g.dragTo(a["to_x"], a["to_y"], duration=max(dur, 0.3), button=a.get("button", "left"))
        elif t == "scroll":
            g.scroll(a["amount"])
        elif t == "type":
            # chunked so ESC can interrupt long text
            txt = a["text"]
            for j in range(0, len(txt), 32):
                STOP.check()
                g.write(txt[j:j + 32], interval=a.get("interval", 0.01))
        elif t == "press":
            g.press(a["key"])
        elif t == "hotkey":
            g.hotkey(*a["keys"])
        elif t == "screenshot":
            if not self.screenshotter:
                raise ActionError("no screenshot backend")
            return self.screenshotter()
        elif t in ("wait_window", "verify_window", "focus_window"):
            if not self.window_waiter:
                raise ActionError("no window backend")
            w = self.window_waiter(a["title"], a.get("timeout", 15 if t == "wait_window" else 2))
            if not w:
                raise ActionError(f"window '{a['title']}' not found")
            if t == "focus_window":
                from .system import window_command
                window_command("focus", a["title"])
            return w
        elif t == "verify_text":
            if not (self.screenshotter and self.ocr):
                raise ActionError("OCR unavailable for verify_text")
            txt = self.ocr(self.screenshotter())
            if a["text"].lower() not in txt.lower():
                raise ActionError(f"text '{a['text']}' not visible on screen")
        return None
