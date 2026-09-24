"""Tool executor: every tool call is assessed, policy-checked, approved if needed,
audited and verified. This is the single choke-point for side effects."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .config import ConfigStore, SCHEMA, apply_mode, ConfigError
from .safety import (Assessment, OpType, Risk, PolicyEngine, Decision, ApprovalBroker, STOP, StopRequested,
                     assess_command, assess_path)
from .audit import AuditLog
from ..computer_control import system as sysctl
from ..computer_control.apps import AppRegistry
from ..computer_control.actions import PyAutoGUIController, dry_run, ActionError


class ToolError(RuntimeError):
    pass


class ApprovalDenied(RuntimeError):
    pass


TOOL_DOCS = {
    "open_app": "open_app(app) — launch an installed application by name/alias",
    "verify_app": "verify_app(app) — check the app process/window exists",
    "window": "window(command, target?) — focus|close|maximize|minimize|next_workspace|prev_workspace|move_resize",
    "run_command": "run_command(command, cwd?, sudo?, timeout?) — run a shell command (shown to user)",
    "read_file": "read_file(path) — read a text file",
    "write_file": "write_file(path, content) — write file (backup made first; root paths need approval)",
    "delete_file": "delete_file(path) — move to trash (undoable)",
    "move_file": "move_file(src, dst)",
    "mkdir": "mkdir(path)",
    "list_dir": "list_dir(path)",
    "edit_file": "edit_file(path) — open in the JARVIS text editor",
    "set_setting": "set_setting(key, value) — change a JARVIS setting",
    "adjust_setting": "adjust_setting(key, delta)",
    "set_mode": "set_mode(mode) — performance|cinematic",
    "map_gesture": "map_gesture(gesture, action)",
    "toggle_gesture": "toggle_gesture(gesture, enabled)",
    "map_clap": "map_clap(count, action)",
    "gui": "gui(actions=[...]) — PyAutoGUI batch: move/click/double_click/right_click/drag/scroll/type/press/hotkey/wait/wait_window/verify_window",
    "screen": "screen(question) — screenshot + vision analysis/OCR",
    "vision_task": "vision_task(goal) — screenshot→plan batch→execute→verify loop for GUI-only apps",
    "mcp": "mcp(tool, args) — call an MCP tool 'server.tool'",
    "blender": "blender(script, blend?, background?) — run Blender Python",
    "unreal": "unreal(project, script) — run Unreal Python via UnrealEditor-Cmd",
    "browser": "browser(url | actions) — open URL / automate with Playwright if installed",
    "notify": "notify(text)",
    "speak": "speak(text)",
    "ask_user": "ask_user(question) — pause and ask",
    "show": "show(what) — settings|mappings|telemetry",
    "stop": "stop() — emergency stop",
}


class ToolExecutor:
    def __init__(self, cfg: ConfigStore, audit: AuditLog, policy: PolicyEngine, approvals: ApprovalBroker,
                 apps: AppRegistry, gui: PyAutoGUIController | None = None, mcp=None, router=None,
                 ui_bus: Callable[[str, dict], None] | None = None):
        self.cfg, self.audit, self.policy, self.approvals = cfg, audit, policy, approvals
        self.apps, self.gui, self.mcp, self.router = apps, gui, mcp, router
        self.ui_bus = ui_bus or (lambda ev, data: None)
        self.undo_stack: list[dict] = []
        self.dry_run = False

    # ------------------------------------------------------------ gatekeeping
    def assess(self, tool: str, args: dict) -> Assessment:
        A = Assessment
        if tool == "run_command":
            a = assess_command(args.get("command", ""))
            if args.get("sudo"):
                a.permissions.append("root")
                a.risk = max(a.risk, Risk.HIGH)
            return a
        if tool in ("write_file",):
            return assess_path(args.get("path", ""), OpType.WRITE)
        if tool == "delete_file":
            return assess_path(args.get("path", ""), OpType.DELETE)
        if tool == "move_file":
            a1, a2 = assess_path(args.get("src", ""), OpType.DELETE), assess_path(args.get("dst", ""), OpType.WRITE)
            return a1 if a1.risk >= a2.risk else a2
        if tool == "mkdir":
            return assess_path(args.get("path", ""), OpType.WRITE)
        if tool in ("read_file", "list_dir", "show", "verify_app", "notify", "speak", "ask_user", "stop", "screen"):
            p = args.get("path")
            if p and tool == "read_file":
                return assess_path(p, OpType.READ)
            return A(Risk.NONE, OpType.READ, [])
        if tool == "screen":
            return A(Risk.LOW, OpType.READ, [])
        if tool in ("set_setting", "adjust_setting", "set_mode", "map_gesture", "map_clap", "toggle_gesture",
                    "train_gesture"):
            key = args.get("key", "")
            if key in SCHEMA and SCHEMA[key].sensitive:
                if key == "control.mode" and args.get("value") in ("safe", "assisted"):
                    return A(Risk.LOW, OpType.WRITE, [], ["lowering autonomy"])  # making it safer is low risk
                return A(Risk.HIGH, OpType.SYSTEM_CHANGE, [], [f"security-relevant setting {key}"])
            act = args.get("action") or {}
            if act.get("type") == "COMMAND":
                return A(Risk.HIGH, OpType.EXECUTE, ["run_commands"], ["mapping executes a shell command"])
            return A(Risk.NONE, OpType.WRITE, [], ["JARVIS preference"])
        if tool in ("open_app", "window", "edit_file"):
            return A(Risk.LOW, OpType.EXECUTE, [], ["opens/controls an application"])
        if tool in ("gui", "vision_task"):
            return A(Risk.MEDIUM, OpType.EXECUTE, ["input_control"], ["controls mouse & keyboard"])
        if tool == "mcp":
            if self.mcp and args.get("tool") in self.mcp.tools:
                return self.mcp.assessment(args["tool"])
            return A(Risk.HIGH, OpType.EXECUTE, ["mcp"], ["unknown MCP tool"])
        if tool in ("blender", "unreal"):
            return A(Risk.MEDIUM, OpType.EXECUTE, ["run_commands"], ["runs application script"])
        if tool == "browser":
            return A(Risk.MEDIUM if args.get("actions") else Risk.LOW, OpType.EXECUTE, ["browser"], ["browser"])
        return A(Risk.HIGH, OpType.EXECUTE, [], [f"unknown tool {tool}"])

    def details(self, tool: str, args: dict) -> str:
        if tool == "run_command":
            return f"$ {'sudo ' if args.get('sudo') else ''}{args.get('command')}\ncwd: {args.get('cwd') or os.getcwd()}"
        if tool == "gui":
            try:
                return "\n".join(dry_run({"actions": args.get("actions", [])}))
            except ActionError as e:
                return f"INVALID BATCH: {e}"
        if tool == "write_file":
            c = args.get("content", "")
            return f"write {len(c)} bytes to {args.get('path')}\n---\n{c[:800]}"
        return json.dumps(args, indent=2, default=str)[:2000]

    def gate(self, tool: str, args: dict, reason: str = "") -> Assessment:
        a = self.assess(tool, args)
        decision, why = self.policy.decide(a)
        key = f"{tool}:{a.risk.name}"
        if decision == Decision.ASK and key in self.approvals.always_allow and not a.critical and a.risk < Risk.HIGH:
            decision = Decision.ALLOW
        self.audit.log("permission", f"{tool} → {decision.value}", tool=tool, risk=a.risk.name, why=why,
                       args=args if tool != "write_file" else {**args, "content": f"<{len(args.get('content',''))} bytes>"})
        if decision == Decision.ASK:
            backup = tool in ("write_file", "delete_file", "move_file") or "file" in " ".join(a.reasons)
            req = self.approvals.request(title=f"{tool}: {self._title(tool, args)}", reason=reason or why,
                                         assessment=a, details=self.details(tool, args), backup_available=backup)
            self.audit.log("approval", f"{req.title} → {req.status}", id=req.id, risk=a.risk.name)
            if req.status == "stop":
                STOP.trigger("user pressed STOP in approval dialog")
                raise StopRequested("stopped from approval dialog")
            if req.status == "approve_all":
                self.approvals.always_allow.add(key)
            elif req.status not in ("approved", "approve"):
                raise ApprovalDenied(f"user denied: {req.title}")
        return a

    @staticmethod
    def _title(tool, args):
        for k in ("command", "path", "app", "key", "tool", "gesture", "goal", "url"):
            if k in args:
                return str(args[k])[:80]
        return ""

    # ------------------------------------------------------------ execution
    def execute(self, tool: str, args: dict, reason: str = "") -> dict:
        STOP.check()
        fn = getattr(self, f"t_{tool}", None)
        if not fn:
            raise ToolError(f"unknown tool '{tool}'. Available: {', '.join(TOOL_DOCS)}")
        self.gate(tool, args, reason)
        if self.dry_run:
            return {"ok": True, "dry_run": True, "details": self.details(tool, args)}
        t0 = time.time()
        try:
            res = fn(**args)
        except (StopRequested, ApprovalDenied):
            raise
        except TypeError as e:
            res = {"ok": False, "error": f"bad arguments for {tool}: {e}"}
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        res.setdefault("ok", True)
        cat = "command" if tool == "run_command" else "file" if tool in ("write_file", "delete_file", "move_file", "mkdir") else "action"
        self.audit.log(cat, f"{tool} {'ok' if res.get('ok') else 'FAILED'}", tool=tool,
                       args={k: v for k, v in args.items() if k != "content"},
                       result={k: (str(v)[:500]) for k, v in res.items()}, duration=round(time.time() - t0, 3))
        if res.get("undo"):
            self.undo_stack.append(res["undo"])
        return res

    # ------------------------------------------------------------ tools
    def t_stop(self):
        STOP.trigger("user command")
        return {"ok": True, "message": "Emergency stop triggered"}

    def t_notify(self, text: str):
        self.ui_bus("notify", {"text": text})
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "JARVIS", text])
        return {"ok": True}

    def t_speak(self, text: str):
        from ..audio.voice import speak
        speak(text)
        return {"ok": True}

    def t_ask_user(self, question: str):
        return {"ok": True, "needs_user": True, "question": question}

    def t_show(self, what: str = "settings"):
        if what == "telemetry":
            return {"ok": True, "data": sysctl.telemetry()}
        if what == "mappings":
            return {"ok": True, "data": {"gestures": self.cfg.get("gestures"), "claps": self.cfg.get("claps")}}
        return {"ok": True, "data": self.cfg.to_dict()["settings"]}

    # settings
    def t_set_setting(self, key: str, value: Any):
        old = self.cfg.get(key)
        new = self.cfg.set(key, value, source="jarvis")
        self.ui_bus("config", {"key": key, "value": new})
        return {"ok": self.cfg.get(key) == new, "key": key, "old": old, "new": new,
                "undo": {"tool": "set_setting", "args": {"key": key, "value": old}}}

    def t_adjust_setting(self, key: str, delta: float):
        s = SCHEMA[key]
        cur = self.cfg.get(key)
        new = cur + delta
        if s.min is not None:
            new = max(s.min, new)
        if s.max is not None:
            new = min(s.max, new)
        new = int(round(new)) if s.type is int else round(new, 3)
        return self.t_set_setting(key, new)

    def t_set_mode(self, mode: str):
        preset = apply_mode(self.cfg, mode, source="jarvis")
        self.ui_bus("config", {"key": "ui.mode", "value": mode})
        return {"ok": self.cfg.get("ui.mode") == mode, "applied": preset}

    def t_map_gesture(self, gesture: str, action: dict, confidence_threshold: float = 0.75, cooldown_ms: int | None = None):
        m = self.cfg.upsert_gesture({"gesture": gesture, "action": action, "enabled": True,
                                     "confidence_threshold": confidence_threshold,
                                     "cooldown_ms": cooldown_ms or self.cfg.get("vision.gesture_cooldown_ms")}, source="jarvis")
        ok = any(x["gesture"] == m["gesture"] and x["action"] == m["action"] for x in self.cfg.get("gestures"))
        return {"ok": ok, "mapping": m}

    def t_toggle_gesture(self, gesture: str, enabled: bool):
        gs = self.cfg.get("gestures")
        found = False
        for g in gs:
            if g["gesture"] == gesture:
                g["enabled"], found = enabled, True
        if not found:
            gs.append({"gesture": gesture, "action": {"type": "NONE"}, "enabled": enabled,
                       "confidence_threshold": 0.75, "cooldown_ms": 1000})
        self.cfg.set_structured("gestures", gs, source="jarvis")
        return {"ok": True}

    def t_map_clap(self, count: int, action: dict):
        m = self.cfg.upsert_clap({"count": count, "action": action, "enabled": True}, source="jarvis")
        return {"ok": any(c["count"] == count and c["action"] == m["action"] for c in self.cfg.get("claps")), "mapping": m}

    def t_train_gesture(self, name: str | None = None):
        self.ui_bus("train_gesture", {"name": name})
        return {"ok": True, "needs_user": True,
                "message": "Hold the gesture in front of the camera; JARVIS will capture 8 samples. Open Settings → Vision & Gestures → Teach gesture."}

    # apps & windows
    def t_open_app(self, app: str):
        from ..apps import workflows
        if app in ("jarvis-files", "jarvis-edit", "jarvis-settings", "jarvis-hud"):
            return workflows.launch_builtin(app)
        return self.apps.launch(app)

    def t_verify_app(self, app: str, timeout: float = 12):
        a = self.apps.resolve(app)
        names = {app.lower()}
        if a:
            names |= {a.name.lower(), (a.argv() or [""])[0].split("/")[-1].lower()}
        end = time.time() + timeout
        while time.time() < end:
            STOP.check()
            try:
                import psutil
                for p in psutil.process_iter(["name", "cmdline"]):
                    cl = " ".join(p.info.get("cmdline") or []).lower()
                    if any(n and (n in (p.info.get("name") or "").lower() or n in cl) for n in names):
                        return {"ok": True, "verified": "process", "pid": p.pid}
            except ImportError:
                r = subprocess.run(["pgrep", "-fi", list(names)[0]], capture_output=True)
                if r.returncode == 0:
                    return {"ok": True, "verified": "process"}
            for w in sysctl.list_windows():
                if any(n in w["title"].lower() for n in names):
                    return {"ok": True, "verified": "window", "window": w["title"]}
            STOP.wait(0.5)
        return {"ok": False, "error": f"could not verify that {app} is running"}

    def t_window(self, command: str, target: str | None = None, **geo):
        return sysctl.window_command(command, target, **geo)

    # terminal
    def t_run_command(self, command: str, cwd: str | None = None, sudo: bool = False, timeout: float | None = None):
        self.ui_bus("terminal", {"command": command, "sudo": sudo})
        r = sysctl.run_command(command, timeout or self.cfg.get("control.action_timeout_s") * 4, cwd, sudo)
        if not r["ok"]:
            r["hints"] = sysctl.diagnose(r)
            r["error"] = r.get("error") or f"exit code {r['code']}: {r['stderr'][-400:]}"
        self.ui_bus("terminal_output", {"command": command, "stdout": r["stdout"][-4000:], "stderr": r["stderr"][-2000:]})
        return r

    # files
    def t_read_file(self, path: str, max_bytes: int = 200_000):
        p = Path(path).expanduser()
        try:
            return {"ok": True, "content": p.read_text(errors="replace")[:max_bytes], "path": str(p)}
        except PermissionError:
            r = subprocess.run(["sudo", "-n", "cat", str(p)], capture_output=True, text=True)
            if r.returncode == 0:
                return {"ok": True, "content": r.stdout[:max_bytes], "path": str(p), "root": True}
            return {"ok": False, "error": "permission denied (root read needs sudo)"}

    def t_write_file(self, path: str, content: str):
        p = Path(path).expanduser()
        root = not os.access(p.parent if not p.exists() else p, os.W_OK)
        r = sysctl.write_file(str(p), content, root=root)
        if r.get("ok"):
            try:
                ok = p.read_text() == content
            except PermissionError:
                ok = True
            r["verified"] = ok
            r["ok"] = ok
            if r.get("backup"):
                r["undo"] = {"tool": "write_file", "args": {"path": str(p), "content_from": r["backup"]}}
        return r

    def t_delete_file(self, path: str):
        r = sysctl.trash(path)
        if r.get("ok"):
            r["ok"] = not Path(path).expanduser().exists()
        return r

    def t_move_file(self, src: str, dst: str):
        s, d = Path(src).expanduser(), Path(dst).expanduser()
        shutil.move(str(s), str(d))
        return {"ok": d.exists() or (d.is_dir() and (d / s.name).exists()),
                "undo": {"tool": "move_file", "args": {"src": str(d), "dst": str(s)}}}

    def t_mkdir(self, path: str):
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path.home() / p
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": p.is_dir(), "path": str(p)}

    def t_list_dir(self, path: str = "~"):
        p = Path(path).expanduser()
        return {"ok": True, "entries": sorted([e.name + ("/" if e.is_dir() else "") for e in p.iterdir()])[:500]}

    def t_edit_file(self, path: str):
        from ..apps import workflows
        return workflows.launch_builtin("jarvis-edit", str(Path(path).expanduser()))

    # GUI
    def t_gui(self, actions: list, timeout: float = 120):
        if not self.gui:
            return {"ok": False, "error": "GUI control unavailable (no display / PyAutoGUI)"}
        res = self.gui.execute({"actions": actions}, retries=self.cfg.get("control.max_retries"), timeout=timeout,
                               on_step=lambda r: self.ui_bus("step", {"desc": str(r.action.get("type")), "ok": r.ok}))
        failed = [r for r in res if not r.ok]
        return {"ok": not failed and len(res) == len(actions), "steps": len(res),
                "error": failed[0].error if failed else None}

    def t_screen(self, question: str = "Describe the screen"):
        from ..vision import screen
        img = screen.capture(self.cfg.get("vision.screenshot_scale"))
        if img is None:
            return {"ok": False, "error": "screenshot unavailable (no display or Pillow/mss missing)"}
        text = screen.ocr(img)
        out = {"ok": True, "ocr": text[:3000]}
        if self.router:
            try:
                ans, prov = self.router.chat([{"role": "user", "content": question, "images": [screen.to_data_url(img)]}], slot="vision")
                out.update(answer=ans, provider=prov)
            except Exception as e:
                out["vision_error"] = str(e)
        return out

    def t_vision_task(self, goal: str, max_rounds: int = 8):
        from .vision_agent import run_vision_task
        return run_vision_task(self, goal, max_rounds)

    # MCP
    def t_mcp(self, tool: str, args: dict | None = None):
        if not self.mcp:
            return {"ok": False, "error": "MCP not configured"}
        return self.mcp.call(tool, args or {})

    # app workflows
    def t_blender(self, script: str, blend: str | None = None, background: bool = True, timeout: float = 600):
        from ..apps import workflows
        return workflows.run_blender(script, blend, background, timeout)

    def t_unreal(self, project: str, script: str, timeout: float = 1800):
        from ..apps import workflows
        return workflows.run_unreal(project, script, timeout)

    def t_browser(self, url: str | None = None, actions: list | None = None):
        from ..apps import workflows
        return workflows.browser(url, actions)
