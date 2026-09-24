"""Deterministic natural-language → plan parser.

Handles the common commands (settings, modes, gesture/clap mappings, launching apps,
windows, files, terminal) with no AI provider at all, so JARVIS stays useful offline.
Anything it cannot parse is handed to the LLM planner.
"""
from __future__ import annotations

import re

WORDNUM = {"once": 1, "one": 1, "single": 1, "twice": 2, "two": 2, "double": 2, "three": 3, "thrice": 3,
           "triple": 3, "four": 4, "five": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
GESTURE_WORDS = [
    (r"open (palm|hand)|palm", "OPEN_PALM"), (r"\bfist\b", "FIST"), (r"thumbs?[ -]?up", "THUMBS_UP"),
    (r"thumbs?[ -]?down", "THUMBS_DOWN"), (r"\bpinch", "PINCH"), (r"swipe (to the )?right", "SWIPE_RIGHT"),
    (r"swipe (to the )?left", "SWIPE_LEFT"), (r"swipe up", "SWIPE_UP"), (r"swipe down", "SWIPE_DOWN"),
    (r"(two|2) fingers?|peace sign|victory", "TWO_FINGERS"), (r"(three|3) fingers?", "THREE_FINGERS"),
    (r"(four|4) fingers?", "FOUR_FINGERS"), (r"(one|1) finger", "ONE_FINGER"), (r"point(ing)?", "POINTING"),
    (r"raise(d)? (my )?hand|hand raised", "HAND_RAISED"),
]
SETTING_WORDS = {
    "brightness": "ui.brightness", "brighter": "ui.brightness", "dimmer": "ui.brightness",
    "transparency": "ui.transparency", "animation speed": "ui.animation_speed", "animations": "ui.animation_speed",
    "camera fps": "vision.fps", "camera frame rate": "vision.fps", "hud fps": "ui.fps",
    "clap sensitivity": "audio.clap_sensitivity", "double-clap timing": "audio.clap_window_ms",
    "double clap timing": "audio.clap_window_ms", "clap window": "audio.clap_window_ms",
    "clap timing": "audio.clap_window_ms", "gesture cooldown": "vision.gesture_cooldown_ms",
    "detection confidence": "vision.detection_confidence", "tracking confidence": "vision.tracking_confidence",
    "pinch sensitivity": "vision.pinch_threshold", "pinch detection": "vision.pinch_threshold",
    "darkness": "ui.darkness", "waveform": "ui.waveform_detail", "camera resolution": "vision.resolution",
}
TOGGLES = {
    "hand tracking": "vision.hand_tracking", "gesture": "vision.hand_tracking", "camera": "vision.hand_tracking",
    "clap detection": "audio.clap_detection", "clap": "audio.clap_detection", "claps": "audio.clap_detection",
    "voice": "audio.voice", "orbital rings": "ui.orbital_rings", "rings": "ui.orbital_rings",
    "particles": "ui.particles", "particle effects": "ui.particles", "reduced motion": "ui.reduced_motion",
    "high contrast": "ui.high_contrast", "memory": "memory.enabled", "overlay": "ui.overlay_visible",
    "hud": "ui.overlay_visible", "auto mode": "ui.auto_mode", "offline mode": "ai.offline_only",
}
POSITIONS = ("left", "right", "top", "bottom")


def step(tool: str, desc: str, **args) -> dict:
    return {"tool": tool, "args": args, "description": desc}


def parse_action_phrase(text: str) -> dict | None:
    """'open VS Code' / 'left click' / 'show the HUD' → universal action dict."""
    t = text.strip().lower().rstrip(".!")
    m = re.match(r"(?:open|launch|start|run)\s+(.+)", t)
    if m:
        return {"type": "OPEN_APPLICATION", "application": m.group(1).strip()}
    if re.search(r"left.?click|click", t) and "right" not in t and "double" not in t:
        return {"type": "MOUSE", "button": "left"}
    if "right" in t and "click" in t:
        return {"type": "MOUSE", "button": "right"}
    if "double" in t and "click" in t:
        return {"type": "MOUSE", "button": "left", "clicks": 2}
    if re.search(r"(show|display|open).*(hud|jarvis|interface|overlay)", t) or t in ("open jarvis",):
        return {"type": "UI", "command": "show_hud"}
    if re.search(r"(hide|close).*(hud|interface|overlay)", t):
        return {"type": "UI", "command": "hide_hud"}
    if re.search(r"(next|change|switch).*(workspace|desktop)", t):
        return {"type": "WINDOW", "command": "next_workspace"}
    if re.search(r"previous.*(workspace|desktop)", t):
        return {"type": "WINDOW", "command": "prev_workspace"}
    if re.search(r"^(confirm|approve|yes)", t):
        return {"type": "APPROVAL", "command": "confirm"}
    if re.search(r"(stop|cancel)", t):
        return {"type": "UI", "command": "stop"}
    if re.search(r"(voice|listen)", t):
        return {"type": "UI", "command": "voice"}
    if re.search(r"(scroll up)", t):
        return {"type": "KEY", "keys": ["pageup"]}
    if re.search(r"(scroll down)", t):
        return {"type": "KEY", "keys": ["pagedown"]}
    if re.search(r"(mute|volume)", t):
        return {"type": "COMMAND", "command": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"}
    m = re.match(r"(?:type|write)\s+(.+)", t)
    if m:
        return {"type": "KEY", "text": m.group(1)}
    return {"type": "AGENT", "goal": text.strip()}


def find_gesture(t: str) -> str | None:
    for pat, g in GESTURE_WORDS:
        if re.search(pat, t):
            return g
    return None


def _num(t: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|ms|milliseconds?|seconds?|s\b)?", t)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2) or ""
    if unit.startswith("s") and not unit.startswith("ms"):
        v *= 1000
    if unit == "%":
        v /= 100
    return v


def parse(text: str) -> list[dict] | None:
    raw = text.strip()
    t = re.sub(r"^(hey |ok |okay )?jarvis[,:]?\s*", "", raw.lower()).strip().rstrip(".!?")
    t = re.sub(r"^(please|can you|could you|would you)\s+", "", t).strip()
    if not t:
        return None

    # ---- recipes (application-API workflows) ----
    if "blender" in t and re.search(r"asset|minecraft|scene|model", t):
        from ..apps.workflows import blender_unreal_plan
        proj = re.search(r"(\S+\.uproject)", raw)
        return blender_unreal_plan(project=proj.group(1) if proj else None)

    # ---- emergency / control ----
    if t in ("stop", "stop jarvis", "cancel", "abort", "halt", "emergency stop"):
        return [step("stop", "Emergency stop")]
    m = re.search(r"(?:switch to|use|enable|set|enter|go to)\s+(safe|assisted|autonomous|full(?: control)?)\s+(?:control\s+)?mode", t)
    if m:
        mode = m.group(1).split()[0]
        return [step("set_setting", f"Set control mode to {mode.upper()}", key="control.mode", value=mode)]

    # ---- visual modes ----
    if re.search(r"performance mode|reduce (the )?visual effects|less (fancy|effects)|save (battery|resources)", t):
        return [step("set_mode", "Switch to Performance Mode", mode="performance")]
    if re.search(r"cinematic mode|more futuristic|full effects|make it (look )?cool", t):
        return [step("set_mode", "Switch to Cinematic Mode", mode="cinematic")]

    # ---- clap mappings ----
    m = re.search(r"when i clap\s+(\w+)(?:\s+times?)?,?\s*(.+)", t)
    if m and m.group(1) in WORDNUM:
        n = WORDNUM[m.group(1)]
        action = parse_action_phrase(m.group(2))
        return [step("map_clap", f"Map {n} clap(s) → {m.group(2)}", count=n, action=action),
                step("set_setting", "Enable clap detection", key="audio.clap_detection", value=True)]
    m = re.search(r"(?:when i )?(single|double|triple)[- ]clap,?\s*(?:to\s+)?(.+)", t)
    if m and "timing" not in t and "window" not in t:
        n = WORDNUM[m.group(1)]
        return [step("map_clap", f"Map {n} clap(s) → {m.group(2)}", count=n, action=parse_action_phrase(m.group(2)))]

    # ---- gesture mappings ----
    m = re.search(r"(?:when i|if i)\s+(?:show|make|do|perform|give)?\s*(?:an?|the)?\s*(.+?),\s*(.+)", t) or \
        re.search(r"(?:when i|if i)\s+(pinch|swipe \w+|raise my hand)\s+(.+)", t) or \
        re.search(r"use\s+(.+?)\s+(?:to|as)\s+(.+)", t)
    if m:
        g = find_gesture(m.group(1))
        if g:
            action_text = m.group(2)
            if t.startswith("use") and not re.match(r"(open|launch|start)", action_text):
                action_text = action_text if "click" in action_text else "open " + action_text
            action = parse_action_phrase(action_text)
            return [step("map_gesture", f"Map {g} → {m.group(2)}", gesture=g, action=action),
                    step("set_setting", "Enable hand tracking", key="vision.hand_tracking", value=True)]
    m = re.search(r"(disable|turn off|enable|turn on)\s+(.+?)\s+(?:gesture|detection)$", t)
    if m:
        g = find_gesture(m.group(2))
        if g:
            on = m.group(1) in ("enable", "turn on")
            return [step("toggle_gesture", f"{'Enable' if on else 'Disable'} {g}", gesture=g, enabled=on)]
    if re.search(r"(teach|train|learn).*(gesture)", t):
        name = re.search(r"(?:called|named)\s+(\w+)", t)
        return [step("train_gesture", "Train a custom gesture", name=name.group(1) if name else None)]

    # ---- sensitivity phrases ----
    if re.search(r"(increase|raise|more).*clap sensitivity|more sensitive.*clap", t):
        return [step("adjust_setting", "Increase clap sensitivity", key="audio.clap_sensitivity", delta=0.1)]
    if re.search(r"(decrease|reduce|lower).*clap sensitivity|reduce false clap|fewer false clap|less sensitive.*clap", t):
        return [step("adjust_setting", "Reduce clap sensitivity", key="audio.clap_sensitivity", delta=-0.1)]
    if re.search(r"pinch.*less sensitive|less sensitive.*pinch", t):
        return [step("adjust_setting", "Make pinch less sensitive", key="vision.pinch_threshold", delta=-0.01)]
    if re.search(r"pinch.*more sensitive|more sensitive.*pinch", t):
        return [step("adjust_setting", "Make pinch more sensitive", key="vision.pinch_threshold", delta=0.01)]

    # ---- move monitor ----
    m = re.search(r"move (?:the )?(?:system )?monitor (?:panel )?to the (left|right|top|bottom)", t)
    if m:
        return [step("set_setting", f"Move system monitor {m.group(1)}", key="ui.monitor_position", value=m.group(1))]

    # ---- comparative UI phrases ----
    for pat, key, delta in ((r"brighter", "ui.brightness", 0.15), (r"dimmer|less bright", "ui.brightness", -0.15),
                            (r"darker", "ui.darkness", 0.1), (r"lighter", "ui.darkness", -0.1),
                            (r"(reduce|less|lower) transparen", "ui.transparency", -0.1),
                            (r"(increase|more) transparen", "ui.transparency", 0.1),
                            (r"(increase|faster).*animation|animation.*faster", "ui.animation_speed", 0.25),
                            (r"(decrease|slower|reduce).*animation|animation.*slower", "ui.animation_speed", -0.25)):
        if re.search(pat, t):
            return [step("adjust_setting", f"Adjust {key} by {delta:+}", key=key, delta=delta)]

    # ---- explicit set X to N ----
    m = re.search(r"(?:set|change|make)\s+(?:the\s+)?(.+?)\s+(?:to|at)\s+(.+)", t)
    if m:
        name, val = m.group(1).strip(), m.group(2).strip()
        key = SETTING_WORDS.get(name) or (name if "." in name else None)
        if not key:
            for w, k in SETTING_WORDS.items():
                if w in name:
                    key = k
                    break
        if key:
            if key in ("vision.resolution",):
                value = val.replace(" ", "")
            elif key.endswith("_ms") or key.endswith("fps") or key == "ui.waveform_detail":
                n = _num(val)
                value = int(n) if n is not None else val
            else:
                n = _num(val)
                value = n if n is not None else val
            return [step("set_setting", f"Set {key} = {value}", key=key, value=value)]
        m2 = re.search(r"(?:hotkey|shortcut) for (\w+)", name)
        if m2:
            return [step("set_setting", f"Set hotkey {m2.group(1)}", key=f"hotkeys.{m2.group(1)}", value=val)]

    # ---- toggles ----
    m = re.search(r"^(disable|turn off|enable|turn on|stop|start|hide|show)\s+(?:the\s+)?(.+)$", t)
    if m:
        what = m.group(2).strip()
        for word, key in sorted(TOGGLES.items(), key=lambda kv: -len(kv[0])):
            if what == word or what.startswith(word):
                on = m.group(1) in ("enable", "turn on", "start", "show")
                return [step("set_setting", f"{'Enable' if on else 'Disable'} {word}", key=key, value=on)]

    # ---- show state ----
    if re.search(r"^(show|what are|list)\s+(my\s+)?(settings|config)", t):
        return [step("show", "Show settings", what="settings")]
    if re.search(r"(show|list).*(gesture|clap) mappings?", t):
        return [step("show", "Show mappings", what="mappings")]
    if re.search(r"(system status|how is the system|telemetry|cpu usage|ram usage|memory usage)", t):
        return [step("show", "Show system status", what="telemetry")]

    # ---- terminal ----
    m = re.match(r"(?:run|execute)\s+(?:the\s+)?(?:command\s+)?[`'\"](.+)[`'\"]$", raw.strip(), re.I) or \
        re.match(r"^\$\s*(.+)$", raw.strip())
    if m:
        return [step("run_command", f"Run: {m.group(1)}", command=m.group(1))]

    # ---- files ----
    m = re.match(r"(?:create|make) (?:a )?(?:folder|directory) (?:called |named )?(.+)", t)
    if m:
        return [step("mkdir", f"Create folder {m.group(1)}", path=m.group(1).strip())]
    m = re.match(r"(?:edit|open) (?:the )?file\s+(\S+)", raw.strip(), re.I) or re.match(r"(?:edit|open)\s+(/\S+|~\S+)$", raw.strip(), re.I)
    if m:
        return [step("edit_file", f"Open {m.group(1)} in JARVIS editor", path=m.group(1))]

    # ---- windows ----
    if re.search(r"(next|switch) (workspace|desktop)", t):
        return [step("window", "Next workspace", command="next_workspace")]
    m = re.match(r"(close|focus|maximize|switch to) (?:the )?(.+?)(?: window)?$", t)
    if m and not re.search(r" and |then", t):
        cmd = {"switch to": "focus"}.get(m.group(1), m.group(1))
        return [step("window", f"{m.group(1)} {m.group(2)}", command=cmd, target=m.group(2))]

    # ---- simple open ----
    m = re.match(r"(?:open|launch|start)\s+(?:the\s+)?(.+)$", t)
    if m and not re.search(r"\b(and|then|create|build|make|write)\b", m.group(1)):
        app = m.group(1).strip()
        if app in ("file manager", "files"):
            app = "jarvis-files"
        return [step("open_app", f"Open {app}", app=app),
                step("verify_app", f"Verify {app} is running", app=app)]
    return None
