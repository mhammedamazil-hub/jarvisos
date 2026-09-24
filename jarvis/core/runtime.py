"""JarvisRuntime: builds and owns all subsystems; exposes a JSON API over a Unix socket.

Every input channel (text, voice, gestures, claps, hotkeys, UI) ends in
`dispatch_action()` or `agent.submit()`, so permissions/audit apply uniformly.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import ConfigStore, jarvis_home, apply_mode
from .audit import AuditLog
from .safety import STOP, PermissionStore, PolicyEngine, ApprovalBroker, StopRequested
from .memory import Memory, TaskStore
from .tools import ToolExecutor, ApprovalDenied
from .agent import Agent, Task
from ..computer_control.apps import AppRegistry
from ..computer_control.actions import PyAutoGUIController
from ..computer_control import system as sysctl
from ..ai.providers import ProviderManager
from ..ai.router import ModelRouter
from ..mcp.client import MCPRegistry


def socket_path() -> Path:
    run = os.environ.get("XDG_RUNTIME_DIR") or str(jarvis_home())
    return Path(run) / "jarvis.sock"


class EventBus:
    def __init__(self):
        self.subs: list[Callable[[str, dict], None]] = []
        self.recent: list[dict] = []

    def emit(self, ev: str, data: dict):
        item = {"ts": time.time(), "event": ev, **data}
        self.recent = (self.recent + [item])[-200:]
        for s in list(self.subs):
            try:
                s(ev, data)
            except Exception:
                pass


class ResourceGovernor(threading.Thread):
    """Low-RAM / high-CPU detection → degrade effects and sensor rates automatically."""

    def __init__(self, rt: "JarvisRuntime", interval: float = 3.0):
        super().__init__(daemon=True, name="resource-governor")
        self.rt, self.interval = rt, interval
        self.load = 0.0          # 0..1 pressure factor used by sensors
        self.degraded = False
        self.last: dict = {}
        self._stop = threading.Event()

    def evaluate(self, t: dict) -> float:
        cfg = self.rt.cfg
        cpu = t.get("cpu", 0) / max(cfg.get("perf.high_cpu_pct"), 1)
        ram = 0.0
        if t.get("ram_avail_mb") is not None:
            ram = max(0.0, 1 - t["ram_avail_mb"] / max(cfg.get("perf.low_ram_mb"), 1)) * 2
        return max(0.0, min(1.0, max(cpu - 0.6, ram) / 0.4 if max(cpu, ram) > 0.6 else 0.0))

    def step(self, t: dict | None = None):
        t = t or sysctl.telemetry()
        self.last = t
        self.load = self.evaluate(t)
        cfg = self.rt.cfg
        if cfg.get("ui.auto_mode"):
            if self.load >= 0.8 and cfg.get("ui.mode") == "cinematic" and not self.degraded:
                self.degraded = True
                apply_mode(cfg, "performance", source="governor")
                self.rt.bus.emit("notify", {"text": "High load: switched to Performance Mode"})
                self.rt.audit.log("system", "auto performance mode", load=self.load)
        self.rt.bus.emit("telemetry", {**t, "load": self.load})

    def run(self):
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception:
                pass

    def stop(self):
        self._stop.set()


class JarvisRuntime:
    def __init__(self, home: Path | None = None, gui_backend=None, headless: bool = False):
        if home:
            os.environ["JARVIS_HOME"] = str(home)
        self.home = jarvis_home()
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.bus = EventBus()
        self.cfg = ConfigStore()
        if not (self.home / "config.json").exists():
            try:
                self.cfg.apply_hardware_profile(sysctl.telemetry().get("ram_total_mb", 0))
            except Exception:
                self.cfg.save()
        self.audit = AuditLog(enabled=self.cfg.get("audit.enabled"))
        self.permissions = PermissionStore()
        self.policy = PolicyEngine(lambda: self.cfg.get("control.mode"), self.permissions)
        self.approvals = ApprovalBroker()
        self.memory = Memory(enabled=self.cfg.get("memory.enabled"))
        self.apps = AppRegistry(self.cfg.get("app_aliases"))
        self.providers = ProviderManager()
        self.router = ModelRouter(self.providers, self.cfg.get)
        self.mcp = MCPRegistry()
        self.gui = None if headless and gui_backend is None else PyAutoGUIController(
            backend=gui_backend, launcher=self.apps.launch, window_waiter=sysctl.wait_for_window,
            pause=self.cfg.get("control.pyautogui_pause"))
        self.tools = ToolExecutor(self.cfg, self.audit, self.policy, self.approvals, self.apps, self.gui,
                                  self.mcp, self.router, ui_bus=self.bus.emit)
        self.agent = Agent(self.tools, self.audit, self.memory, TaskStore(), self.router,
                           on_update=lambda t: self.bus.emit("task", self._task_brief(t)))
        self.governor = ResourceGovernor(self)
        self.sensors: dict[str, Any] = {}
        self.sensor_status: dict[str, str] = {"hand_tracking": "off", "clap": "off", "voice": "off"}
        self.cfg.on_change(self._on_config)
        STOP.on_trigger(lambda: (self.bus.emit("stop", {"reason": STOP.reason}),
                                 self.audit.log("system", "EMERGENCY STOP", reason=STOP.reason)))

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.governor.start()
        self._sync_sensors()
        threading.Thread(target=self.mcp.connect_all, daemon=True).start()
        self.audit.log("system", "JARVIS core started", mode=self.cfg.get("control.mode"))

    def shutdown(self):
        self.governor.stop()
        for s in self.sensors.values():
            s.stop()
        for n in list(self.mcp.clients):
            self.mcp.disconnect(n)

    def _on_config(self, key, old, new):
        if key in ("vision.hand_tracking", "audio.clap_detection", "audio.voice", "vision.camera_index",
                   "vision.resolution", "audio.microphone"):
            self._sync_sensors(restart=key in ("vision.camera_index", "vision.resolution", "audio.microphone"))
        if key.startswith("audio.clap") and "clap" in self.sensors:
            self.sensors["clap"].det.set_params(self.cfg.get("audio.clap_sensitivity"), self.cfg.get("audio.clap_window_ms"),
                                                self.cfg.get("audio.clap_cooldown_ms"), self.cfg.get("audio.clap_min_gap_ms"))
        if key == "app_aliases":
            self.apps.aliases = {k.lower(): v for k, v in new.items()}
        if key.startswith("vision.") and "hand" in self.sensors:
            e = self.sensors["hand"].engine
            e.confirm_frames = self.cfg.get("vision.confirm_frames")
            e.pinch_threshold = self.cfg.get("vision.pinch_threshold")
            e.default_cooldown_ms = self.cfg.get("vision.gesture_cooldown_ms")
        if key == "audit.enabled":
            self.audit.enabled = bool(new)
        if key == "memory.enabled":
            self.memory.enabled = bool(new)

    def _status(self, name):
        def f(s):
            self.sensor_status[name] = s
            self.bus.emit("sensor", {"name": name, "status": s})
            if s.startswith(("error", "unavailable")):
                self.audit.log("error", f"{name}: {s}")
        return f

    def _sync_sensors(self, restart: bool = False):
        from ..vision.gestures import GestureEngine, HandTracker
        from ..audio.clap import ClapDetector, MicListener
        want_hand = self.cfg.get("vision.hand_tracking")
        if restart or not want_hand:
            s = self.sensors.pop("hand", None)
            if s:
                s.stop()
                self.sensor_status["hand_tracking"] = "off"
        if want_hand and "hand" not in self.sensors:
            eng = GestureEngine(lambda: self.cfg.get("gestures"), self.cfg.get("vision.confirm_frames"),
                                self.cfg.get("vision.gesture_cooldown_ms"), self.cfg.get("vision.pinch_threshold"),
                                customs_getter=lambda: self.cfg.get("custom_gestures"))
            ht = HandTracker(eng, self.on_gesture, self.cfg.get, self._status("hand_tracking"),
                             load_factor=lambda: self.governor.load)
            self.sensors["hand"] = ht
            ht.start()
        want_clap = self.cfg.get("audio.clap_detection")
        if restart or not want_clap:
            s = self.sensors.pop("clap", None)
            if s:
                s.stop()
                self.sensor_status["clap"] = "off"
        if want_clap and "clap" not in self.sensors:
            det = ClapDetector(self.cfg.get("audio.clap_sensitivity"), self.cfg.get("audio.clap_window_ms"),
                               self.cfg.get("audio.clap_cooldown_ms"), self.cfg.get("audio.clap_min_gap_ms"),
                               max_count=max([c["count"] for c in self.cfg.get("claps")] or [3]))
            ml = MicListener(det, self.on_clap, self.cfg.get("audio.microphone"), self._status("clap"))
            self.sensors["clap"] = ml
            ml.start()

    # ------------------------------------------------------------ input channels
    def on_gesture(self, ev):
        m = next((g for g in self.cfg.get("gestures") if g["gesture"] == ev.gesture and g["enabled"]), None)
        self.audit.log("gesture", ev.gesture, confidence=round(ev.confidence, 2), mapped=bool(m))
        self.bus.emit("gesture", {"gesture": ev.gesture, "confidence": ev.confidence})
        if m:
            self.dispatch_action(m["action"], source=f"gesture:{ev.gesture}")

    def on_clap(self, ev):
        m = next((c for c in self.cfg.get("claps") if c["count"] == ev.count and c["enabled"]), None)
        self.audit.log("clap", f"{ev.count} clap(s)", mapped=bool(m))
        self.bus.emit("clap", {"count": ev.count})
        if m:
            self.dispatch_action(m["action"], source=f"clap:{ev.count}")

    def dispatch_action(self, action: dict, source: str = "ui") -> dict:
        """Universal action router shared by gestures, claps, hotkeys and the UI."""
        t = action.get("type", "NONE").upper()
        try:
            if t == "OPEN_APPLICATION":
                return self.tools.execute("open_app", {"app": action["application"]}, reason=source)
            if t == "UI":
                cmd = action.get("command")
                if cmd == "stop":
                    STOP.trigger(source)
                    return {"ok": True}
                if cmd in ("show_hud", "hide_hud"):
                    self.cfg.set("ui.overlay_visible", cmd == "show_hud", source=source)
                self.bus.emit("ui", {"command": cmd, "source": source})
                return {"ok": True}
            if t == "WINDOW":
                return self.tools.execute("window", {"command": action["command"], "target": action.get("target")}, reason=source)
            if t == "MOUSE":
                return self.tools.execute("gui", {"actions": [{"type": "click", "button": action.get("button", "left"),
                                                                "clicks": action.get("clicks", 1)}]}, reason=source)
            if t == "KEY":
                acts = [{"type": "hotkey", "keys": action["keys"]}] if action.get("keys") else [{"type": "type", "text": action.get("text", "")}]
                return self.tools.execute("gui", {"actions": acts}, reason=source)
            if t == "APPROVAL":
                pend = next(iter(self.approvals.pending), None)
                return {"ok": bool(pend and self.approvals.respond(pend, "approved"))}
            if t == "COMMAND":
                return self.tools.execute("run_command", {"command": action["command"]}, reason=source)
            if t == "MCP":
                return self.tools.execute("mcp", {"tool": action["tool"], "args": action.get("args", {})}, reason=source)
            if t == "SETTING":
                return self.tools.execute("set_setting", {"key": action["key"], "value": action["value"]}, reason=source)
            if t == "AGENT":
                task = self.agent.submit(action["goal"], source=source)
                return {"ok": True, "task": task.id}
            return {"ok": False, "error": f"no handler for {t}"}
        except (ApprovalDenied, StopRequested) as e:
            return {"ok": False, "error": str(e)}

    def command(self, text: str, source: str = "text", wait: bool = False, timeout: float = 600) -> dict:
        task = self.agent.submit(text, source=source, background=not wait)
        if wait:
            return {"task": self._task_brief(task), "report": self.agent.report(task)}
        return {"task": self._task_brief(task)}

    @staticmethod
    def _task_brief(t: Task) -> dict:
        d, n = t.progress()
        return {"id": t.id, "goal": t.goal, "status": t.status, "progress": [d, n], "error": t.error,
                "steps": [{"desc": s.description or s.tool, "tool": s.tool, "status": s.status} for s in t.steps]}

    # ------------------------------------------------------------ API
    def api(self, method: str, p: dict) -> Any:
        if method == "command":
            return self.command(p["text"], p.get("source", "text"), p.get("wait", False))
        if method == "task":
            t = self.agent.tasks.get(p["id"])
            return {"task": self._task_brief(t), "report": self.agent.report(t)} if t else None
        if method == "tasks":
            return [self._task_brief(t) for t in list(self.agent.tasks.values())[-30:]]
        if method == "stop":
            STOP.trigger(p.get("reason", "user"))
            self.agent.cancel()
            return {"ok": True}
        if method in ("pause", "resume"):
            getattr(self.agent, method)()
            return {"ok": True}
        if method == "config.get":
            return self.cfg.to_dict() if not p.get("key") else self.cfg.describe(p["key"])
        if method == "config.set":
            return self.tools.execute("set_setting", {"key": p["key"], "value": p["value"]}, reason="settings UI")
        if method == "config.mode":
            return self.tools.execute("set_mode", {"mode": p["mode"]})
        if method == "config.structured":
            self.cfg.set_structured(p["key"], p["value"], source="settings UI")
            return {"ok": True}
        if method == "approvals":
            return [vars(r) for r in self.approvals.pending.values()]
        if method == "approve":
            return {"ok": self.approvals.respond(p["id"], p.get("status", "approved"))}
        if method == "status":
            return {"mode": self.cfg.get("control.mode"), "ui_mode": self.cfg.get("ui.mode"),
                    "telemetry": self.governor.last, "load": self.governor.load, "sensors": self.sensor_status,
                    "stop": STOP.active, "providers": [{"name": x["name"], "has_key": x["has_key"], "health": x["health"]}
                                                       for x in self.providers.public()],
                    "mcp": self.mcp.status, "pending_approvals": len(self.approvals.pending)}
        if method == "audit":
            return self.audit.entries(p.get("limit", 100), p.get("category"), p.get("query"))
        if method == "events":
            since = p.get("since", 0)
            return [e for e in self.bus.recent if e["ts"] > since]
        if method == "apps":
            self.apps.refresh()
            return sorted([{"id": a.id, "name": a.name, "icon": a.icon, "categories": a.categories}
                           for a in self.apps.apps.values()], key=lambda a: a["name"].lower())
        if method == "launch":
            return self.tools.execute("open_app", {"app": p["app"]}, reason="launcher")
        if method == "action":
            return self.dispatch_action(p["action"], source=p.get("source", "ui"))
        if method == "permissions":
            return self.permissions.list()
        if method == "permission.set":
            self.permissions.set(p["name"], bool(p["value"]))
            self.audit.log("permission", f"{p['name']} set to {p['value']}")
            return {"ok": True}
        if method == "providers":
            return self.providers.public()
        if method == "provider.add":
            pc = self.providers.add(p["name"], p.get("template"), p.get("api_key"),
                                    **{k: v for k, v in p.items() if k in ("adapter", "base_url", "models", "timeout", "max_tokens", "vision", "local", "needs_key")})
            self.audit.log("config", f"provider added {p['name']}")
            return {"ok": True, "name": pc.name}
        if method == "provider.remove":
            self.providers.remove(p["name"])
            return {"ok": True}
        if method == "provider.test":
            return self.providers.test(p["name"])
        if method == "mcp.list":
            return {"servers": {n: {"enabled": s.enabled, "status": self.mcp.status.get(n, "disconnected")}
                                for n, s in self.mcp.servers.items()}, "tools": self.mcp.tools}
        if method == "memory":
            return self.memory.data
        if method == "memory.clear":
            self.memory.clear()
            return {"ok": True}
        raise ValueError(f"unknown method {method}")

    # ------------------------------------------------------------ socket server
    def serve(self, path: Path | None = None):
        path = path or socket_path()
        path.unlink(missing_ok=True)
        rt = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    try:
                        req = json.loads(line)
                        res = {"id": req.get("id"), "result": rt.api(req["method"], req.get("params", {}))}
                    except Exception as e:
                        res = {"id": None, "error": f"{type(e).__name__}: {e}"}
                    self.wfile.write((json.dumps(res, default=str) + "\n").encode())

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        srv = Server(str(path), Handler)
        os.chmod(path, 0o600)
        self.server = srv
        return srv


class Client:
    """Tiny client for the daemon socket (used by CLI and UI)."""

    def __init__(self, path: Path | None = None, timeout: float = 30):
        self.path, self.timeout, self._id = path or socket_path(), timeout, 0

    def call(self, method: str, **params):
        self._id += 1
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            s.connect(str(self.path))
            s.sendall((json.dumps({"id": self._id, "method": method, "params": params}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        r = json.loads(buf)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"]

    def alive(self) -> bool:
        try:
            self.call("status")
            return True
        except OSError:
            return False
