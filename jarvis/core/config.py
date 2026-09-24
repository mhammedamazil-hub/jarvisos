"""Centralized, validated configuration for JARVISOS.

Every setting is declared in SCHEMA with type, default and constraints. The UI,
the agent and the CLI all read/write through ConfigStore, so the AI can change
any setting the user can (subject to permission checks upstream).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CONFIG_VERSION = 1


def jarvis_home() -> Path:
    base = os.environ.get("JARVIS_HOME")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(xdg) / "jarvis"


@dataclass
class Setting:
    type: type
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    description: str = ""
    sensitive: bool = False        # changing it needs elevated approval
    restart: bool = False


S = Setting
SCHEMA: dict[str, Setting] = {
    # ---- UI ----
    "ui.mode": S(str, "performance", choices=("performance", "cinematic"), description="Visual mode"),
    "ui.auto_mode": S(bool, True, description="Auto-switch to performance mode under load / low RAM"),
    "ui.brightness": S(float, 1.0, 0.3, 2.0, description="HUD brightness multiplier"),
    "ui.darkness": S(float, 0.85, 0.0, 1.0, description="Background darkness"),
    "ui.transparency": S(float, 0.15, 0.0, 0.8, description="Panel transparency (0=opaque)"),
    "ui.animation_speed": S(float, 1.0, 0.1, 3.0, description="Animation speed multiplier"),
    "ui.orbital_rings": S(bool, True, description="Show orbital rings around the core"),
    "ui.particles": S(bool, False, description="Particle effects (cinematic)"),
    "ui.waveform_detail": S(int, 24, 4, 128, description="Waveform bars"),
    "ui.fps": S(int, 20, 1, 60, description="HUD redraw rate"),
    "ui.reduced_motion": S(bool, False, description="Accessibility: minimise motion"),
    "ui.high_contrast": S(bool, False),
    "ui.monitor_position": S(str, "right", choices=("left", "right", "top", "bottom", "hidden")),
    "ui.overlay_visible": S(bool, True),
    "ui.accent": S(str, "#29d8ff", description="Accent colour hex"),
    # ---- control ----
    "control.mode": S(str, "safe", choices=("safe", "assisted", "autonomous", "full"), sensitive=True),
    "control.max_retries": S(int, 2, 0, 10),
    "control.action_timeout_s": S(float, 30.0, 1, 600),
    "control.max_steps": S(int, 40, 1, 500, description="Hard step limit per task (no infinite loops)"),
    "control.pyautogui_pause": S(float, 0.08, 0.0, 2.0),
    "control.esc_stops": S(bool, True),
    # ---- vision ----
    "vision.hand_tracking": S(bool, False),
    "vision.camera_index": S(int, 0, 0, 16),
    "vision.fps": S(int, 10, 1, 30),
    "vision.resolution": S(str, "320x240", choices=("160x120", "320x240", "640x480", "1280x720")),
    "vision.detection_confidence": S(float, 0.6, 0.1, 0.99),
    "vision.tracking_confidence": S(float, 0.5, 0.1, 0.99),
    "vision.gesture_cooldown_ms": S(int, 1000, 0, 10000),
    "vision.confirm_frames": S(int, 4, 1, 30, description="Frames a gesture must persist before it fires"),
    "vision.pinch_threshold": S(float, 0.05, 0.01, 0.2, description="Normalised thumb-index distance for pinch"),
    "vision.device": S(str, "auto", choices=("auto", "cpu", "gpu")),
    "vision.screenshot_scale": S(float, 0.5, 0.1, 1.0),
    # ---- audio ----
    "audio.clap_detection": S(bool, False),
    "audio.voice": S(bool, False),
    "audio.microphone": S(str, "default"),
    "audio.clap_sensitivity": S(float, 0.5, 0.0, 1.0, description="Higher = more sensitive"),
    "audio.clap_window_ms": S(int, 600, 150, 2000, description="Max gap between claps in a sequence"),
    "audio.clap_cooldown_ms": S(int, 1500, 0, 10000),
    "audio.clap_min_gap_ms": S(int, 120, 30, 500),
    "audio.voice_provider": S(str, "vosk", choices=("vosk", "whisper_local", "cloud", "none")),
    "audio.wake_phrase": S(str, "jarvis"),
    "audio.push_to_talk": S(bool, True),
    "audio.cloud_stt_allowed": S(bool, False, sensitive=True),
    # ---- AI ----
    "ai.route.fast": S(str, ""),
    "ai.route.vision": S(str, ""),
    "ai.route.reasoning": S(str, ""),
    "ai.route.coding": S(str, ""),
    "ai.route.local": S(str, ""),
    "ai.offline_only": S(bool, False),
    # ---- memory / audit ----
    "memory.enabled": S(bool, True),
    "audit.enabled": S(bool, True, sensitive=True),
    "audit.log_commands": S(bool, True),
    "audit.log_screenshots": S(bool, False),
    # ---- hotkeys ----
    "hotkeys.command": S(str, "super+space"),
    "hotkeys.voice": S(str, "super+v"),
    "hotkeys.launcher": S(str, "super+a"),
    "hotkeys.files": S(str, "super+f"),
    "hotkeys.stop": S(str, "Escape"),
    # ---- performance ----
    "perf.low_ram_mb": S(int, 700, 100, 8000, description="Available RAM below which we degrade"),
    "perf.high_cpu_pct": S(float, 85.0, 20, 100),
}

# Structured (list/dict) sections validated by dedicated validators.
DEFAULT_STRUCTURED: dict[str, Any] = {
    "gestures": [
        {"gesture": "OPEN_PALM", "action": {"type": "UI", "command": "show_hud"}, "enabled": True, "confidence_threshold": 0.75, "cooldown_ms": 1000},
        {"gesture": "FIST", "action": {"type": "UI", "command": "hide_hud"}, "enabled": True, "confidence_threshold": 0.75, "cooldown_ms": 1000},
        {"gesture": "PINCH", "action": {"type": "MOUSE", "button": "left"}, "enabled": False, "confidence_threshold": 0.8, "cooldown_ms": 400},
        {"gesture": "SWIPE_RIGHT", "action": {"type": "WINDOW", "command": "next_workspace"}, "enabled": True, "confidence_threshold": 0.7, "cooldown_ms": 1200},
        {"gesture": "SWIPE_LEFT", "action": {"type": "WINDOW", "command": "prev_workspace"}, "enabled": True, "confidence_threshold": 0.7, "cooldown_ms": 1200},
        {"gesture": "THUMBS_UP", "action": {"type": "APPROVAL", "command": "confirm"}, "enabled": True, "confidence_threshold": 0.85, "cooldown_ms": 1500},
    ],
    "claps": [
        {"count": 1, "action": {"type": "OPEN_APPLICATION", "application": "discord"}, "enabled": False},
        {"count": 2, "action": {"type": "OPEN_APPLICATION", "application": "code"}, "enabled": True},
        {"count": 3, "action": {"type": "UI", "command": "show_hud"}, "enabled": True},
    ],
    "app_aliases": {"vs code": "code", "vscode": "code", "browser": "firefox-esr", "files": "jarvis-files",
                    "terminal": "x-terminal-emulator", "editor": "jarvis-edit"},
    "custom_gestures": {},
}

GESTURE_NAMES = {"OPEN_PALM", "FIST", "POINTING", "THUMBS_UP", "THUMBS_DOWN", "ONE_FINGER", "TWO_FINGERS",
                 "THREE_FINGERS", "FOUR_FINGERS", "PINCH", "SWIPE_LEFT", "SWIPE_RIGHT", "SWIPE_UP",
                 "SWIPE_DOWN", "HAND_RAISED"}
ACTION_TYPES = {"OPEN_APPLICATION", "UI", "MOUSE", "WINDOW", "APPROVAL", "COMMAND", "KEY", "MCP", "SETTING",
                "AGENT", "NONE"}


class ConfigError(ValueError):
    pass


def coerce(key: str, value: Any) -> Any:
    if key not in SCHEMA:
        raise ConfigError(f"unknown setting '{key}'")
    s = SCHEMA[key]
    try:
        if s.type is bool:
            if isinstance(value, str):
                v = value.strip().lower()
                if v in ("1", "true", "on", "yes", "enable", "enabled"):
                    value = True
                elif v in ("0", "false", "off", "no", "disable", "disabled"):
                    value = False
                else:
                    raise ValueError(value)
            value = bool(value)
        elif s.type is int:
            value = int(float(value))
        elif s.type is float:
            value = float(value)
        else:
            value = str(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{key}: expected {s.type.__name__}, got {value!r}")
    if s.min is not None and value < s.min:
        raise ConfigError(f"{key}: {value} below minimum {s.min}")
    if s.max is not None and value > s.max:
        raise ConfigError(f"{key}: {value} above maximum {s.max}")
    if s.choices and value not in s.choices:
        raise ConfigError(f"{key}: {value!r} not in {s.choices}")
    return value


def validate_gesture_mapping(m: dict) -> dict:
    if not isinstance(m, dict):
        raise ConfigError("gesture mapping must be an object")
    g = str(m.get("gesture", "")).upper()
    if g not in GESTURE_NAMES and not g.startswith("CUSTOM:"):
        raise ConfigError(f"unknown gesture {g}")
    a = m.get("action") or {}
    if str(a.get("type", "")).upper() not in ACTION_TYPES:
        raise ConfigError(f"invalid action type {a.get('type')}")
    ct = float(m.get("confidence_threshold", 0.75))
    cd = int(m.get("cooldown_ms", 1000))
    if not 0 < ct <= 1 or not 0 <= cd <= 60000:
        raise ConfigError("confidence_threshold must be (0,1], cooldown_ms 0..60000")
    return {"gesture": g, "action": {**a, "type": a["type"].upper()}, "enabled": bool(m.get("enabled", True)),
            "confidence_threshold": ct, "cooldown_ms": cd}


def validate_clap_mapping(m: dict) -> dict:
    c = int(m.get("count", 0))
    if not 1 <= c <= 5:
        raise ConfigError("clap count must be 1..5")
    a = m.get("action") or {}
    if str(a.get("type", "")).upper() not in ACTION_TYPES:
        raise ConfigError(f"invalid action type {a.get('type')}")
    return {"count": c, "action": {**a, "type": a["type"].upper()}, "enabled": bool(m.get("enabled", True))}


class ConfigStore:
    """Thread-safe JSON config with validation, backups, history and listeners."""

    MAX_BACKUPS = 20

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else jarvis_home() / "config.json"
        self.backup_dir = self.path.parent / "backups"
        self.history_path = self.path.parent / "config-history.jsonl"
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, Any, Any], None]] = []
        self.values: dict[str, Any] = {}
        self.structured: dict[str, Any] = {}
        self.load()

    # ---------- persistence ----------
    def defaults(self) -> dict[str, Any]:
        return {k: s.default for k, s in SCHEMA.items()}

    def load(self) -> list[str]:
        """Load config; invalid values fall back to defaults. Returns warnings."""
        warnings: list[str] = []
        with self._lock:
            self.values = self.defaults()
            self.structured = copy.deepcopy(DEFAULT_STRUCTURED)
            if not self.path.exists():
                return warnings
            try:
                raw = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                warnings.append(f"config unreadable ({e}); using defaults")
                return warnings
            raw = self._migrate(raw)
            for k, v in (raw.get("settings") or {}).items():
                try:
                    self.values[k] = coerce(k, v)
                except ConfigError as e:
                    warnings.append(f"{e}; using default")
            for key, validator in (("gestures", validate_gesture_mapping), ("claps", validate_clap_mapping)):
                if key in raw:
                    ok = []
                    for m in raw[key]:
                        try:
                            ok.append(validator(m))
                        except (ConfigError, TypeError, ValueError, KeyError) as e:
                            warnings.append(f"{key}: dropped invalid mapping ({e})")
                    self.structured[key] = ok
            for key in ("app_aliases", "custom_gestures"):
                if isinstance(raw.get(key), dict):
                    self.structured[key] = raw[key]
        self.load_warnings = warnings
        return warnings

    def _migrate(self, raw: dict) -> dict:
        v = raw.get("version", 1)
        if v > CONFIG_VERSION:
            # Newer config: keep known keys only.
            pass
        return raw

    def to_dict(self) -> dict:
        with self._lock:
            return {"version": CONFIG_VERSION, "settings": dict(self.values), **copy.deepcopy(self.structured)}

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            if self.path.exists():
                self._backup()
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
            os.replace(tmp, self.path)

    def _backup(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dst = self.backup_dir / f"config-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**6}.json"
        shutil.copy2(self.path, dst)
        backups = sorted(self.backup_dir.glob("config-*.json"))
        for old in backups[: -self.MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def backups(self) -> list[Path]:
        return sorted(self.backup_dir.glob("config-*.json")) if self.backup_dir.exists() else []

    def restore_backup(self, path: Path | None = None) -> None:
        b = path or (self.backups()[-1] if self.backups() else None)
        if not b:
            raise ConfigError("no backups available")
        shutil.copy2(b, self.path)
        self.load()

    def export(self, dest: Path) -> None:
        Path(dest).write_text(json.dumps(self.to_dict(), indent=2))

    def import_(self, src: Path) -> list[str]:
        data = json.loads(Path(src).read_text())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._backup()
        self.path.write_text(json.dumps(data))
        w = self.load()
        self.save()
        return w

    def reset(self, key: str | None = None) -> None:
        if key:
            self.set(key, SCHEMA[key].default, source="reset")
        else:
            with self._lock:
                self.values = self.defaults()
                self.structured = copy.deepcopy(DEFAULT_STRUCTURED)
            self._record("*", None, "defaults", "reset")
            self.save()

    # ---------- access ----------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self.values:
                return self.values[key]
            if key in self.structured:
                return copy.deepcopy(self.structured[key])
            return default

    def set(self, key: str, value: Any, source: str = "user", save: bool = True) -> Any:
        v = coerce(key, value)
        with self._lock:
            old = self.values.get(key)
            self.values[key] = v
        self._record(key, old, v, source)
        if save:
            self.save()
        for cb in list(self._listeners):
            try:
                cb(key, old, v)
            except Exception:
                pass
        return v

    def set_structured(self, key: str, value: Any, source: str = "user") -> None:
        if key == "gestures":
            value = [validate_gesture_mapping(m) for m in value]
        elif key == "claps":
            value = [validate_clap_mapping(m) for m in value]
        elif key not in DEFAULT_STRUCTURED:
            raise ConfigError(f"unknown section {key}")
        with self._lock:
            old = self.structured.get(key)
            self.structured[key] = value
        self._record(key, "<structured>", "<structured>", source)
        self.save()
        for cb in list(self._listeners):
            try:
                cb(key, old, value)
            except Exception:
                pass

    def upsert_gesture(self, mapping: dict, source: str = "user") -> dict:
        m = validate_gesture_mapping(mapping)
        g = [x for x in self.get("gestures") if x["gesture"] != m["gesture"]] + [m]
        self.set_structured("gestures", g, source)
        return m

    def upsert_clap(self, mapping: dict, source: str = "user") -> dict:
        m = validate_clap_mapping(mapping)
        c = [x for x in self.get("claps") if x["count"] != m["count"]] + [m]
        self.set_structured("claps", sorted(c, key=lambda x: x["count"]), source)
        return m

    def on_change(self, cb: Callable[[str, Any, Any], None]) -> None:
        self._listeners.append(cb)

    def _record(self, key, old, new, source) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "a") as f:
                f.write(json.dumps({"ts": time.time(), "key": key, "old": old, "new": new, "source": source}) + "\n")
        except OSError:
            pass

    def history(self, limit: int = 50) -> list[dict]:
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text().splitlines()[-limit:]
        return [json.loads(l) for l in lines if l.strip()]

    def describe(self, key: str) -> dict:
        s = SCHEMA[key]
        return {"key": key, "value": self.get(key), "default": s.default, "type": s.type.__name__,
                "min": s.min, "max": s.max, "choices": s.choices, "description": s.description,
                "sensitive": s.sensitive}

    # ---------- hardware awareness ----------
    def apply_hardware_profile(self, total_ram_mb: int) -> list[str]:
        """On first run choose sane defaults for the machine."""
        changed = []
        if total_ram_mb and total_ram_mb <= 4096:
            for k, v in (("ui.mode", "performance"), ("ui.particles", False), ("ui.fps", 15),
                         ("vision.fps", 8), ("vision.resolution", "320x240")):
                if self.get(k) != v:
                    self.set(k, v, source="hardware", save=False)
                    changed.append(k)
            self.save()
        return changed


MODE_PRESETS = {
    "performance": {"ui.mode": "performance", "ui.transparency": 0.0, "ui.particles": False,
                    "ui.orbital_rings": False, "ui.waveform_detail": 12, "ui.fps": 12, "vision.fps": 8},
    "cinematic": {"ui.mode": "cinematic", "ui.transparency": 0.25, "ui.particles": True,
                  "ui.orbital_rings": True, "ui.waveform_detail": 64, "ui.fps": 30},
}


def apply_mode(cfg: ConfigStore, mode: str, source: str = "user") -> dict:
    preset = MODE_PRESETS[mode]
    for k, v in preset.items():
        cfg.set(k, v, source=source, save=False)
    cfg.save()
    return preset
