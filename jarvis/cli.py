"""`jarvis` command-line interface. Talks to jarvisd over its socket, or runs
in-process with `--local` (useful offline / in recovery)."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import signal
import sys
import time

C = "\033[96m"
Y = "\033[93m"
R = "\033[91m"
G = "\033[92m"
D = "\033[2m"
X = "\033[0m"


def approval_prompt(req) -> str:
    a = req.assessment if isinstance(req.assessment, dict) else req["assessment"]
    get = (lambda k: getattr(req, k)) if not isinstance(req, dict) else req.get
    print(f"\n{Y}╔══ JARVIS REQUESTS APPROVAL ══════════════════════{X}")
    print(f"{Y}║{X} Action:     {get('title')}")
    print(f"{Y}║{X} Reason:     {get('reason')}")
    print(f"{Y}║{X} Risk:       {a['risk']}   Operation: {a['op']}   Reversible: {'yes' if a['reversible'] else 'NO'}")
    print(f"{Y}║{X} Backup:     {'available' if get('backup_available') else 'n/a'}")
    print(f"{Y}║{X} Details:\n{D}" + "\n".join("    " + l for l in get("details").splitlines()) + X)
    print(f"{Y}╚═ [a]pprove  [A]pprove all similar  [d]eny  [s]top jarvis{X}")
    try:
        ans = input("> ").strip()
    except EOFError:
        return "denied"
    return {"a": "approved", "y": "approved", "A": "approve_all", "s": "stop"}.get(ans, "denied")


def local_runtime():
    from .core.runtime import JarvisRuntime
    rt = JarvisRuntime()
    rt.approvals.handler = approval_prompt
    return rt


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis", description="JARVISOS command interface")
    ap.add_argument("--local", action="store_true", help="run in-process instead of via jarvisd")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("daemon", help="run the JARVIS core daemon")
    d = sub.add_parser("do", help="give JARVIS a command/goal")
    d.add_argument("text", nargs="+")
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("stop", help="EMERGENCY STOP")
    sub.add_parser("status")
    sub.add_parser("doctor", help="diagnose optional components")
    cf = sub.add_parser("config")
    cf.add_argument("action", choices=["get", "set", "list", "reset", "export", "import", "history", "restore", "mode"])
    cf.add_argument("args", nargs="*")
    pv = sub.add_parser("provider")
    pv.add_argument("action", choices=["list", "add", "remove", "test", "model", "route"])
    pv.add_argument("name", nargs="?")
    pv.add_argument("--template")
    pv.add_argument("--base-url")
    pv.add_argument("--model", action="append", default=[], help="slot=model, e.g. text=mistral-small-latest")
    pv.add_argument("--key", action="store_true", help="prompt for API key (never echoed)")
    pv.add_argument("--vision", action="store_true")
    pv.add_argument("--local-model", action="store_true")
    mc = sub.add_parser("mcp")
    mc.add_argument("action", choices=["list", "add", "remove", "test", "enable-tool", "disable-tool"])
    mc.add_argument("name", nargs="?")
    mc.add_argument("--command")
    mc.add_argument("--url")
    au = sub.add_parser("audit")
    au.add_argument("--category")
    au.add_argument("--search")
    au.add_argument("-n", type=int, default=40)
    au.add_argument("--export")
    au.add_argument("--clear", action="store_true")
    pm = sub.add_parser("permissions")
    pm.add_argument("name", nargs="?")
    pm.add_argument("value", nargs="?", choices=["on", "off"])
    sub.add_parser("apps")
    sub.add_parser("approvals", help="answer pending approvals interactively")
    mem = sub.add_parser("memory")
    mem.add_argument("action", choices=["show", "clear", "export", "import"])
    mem.add_argument("path", nargs="?")
    for n in ("hud", "files", "settings"):
        sub.add_parser(n)
    e = sub.add_parser("edit")
    e.add_argument("path", nargs="?")
    sub.add_parser("voice", help="push-to-talk: listen once and run the command")
    sub.add_parser("repl", help="interactive JARVIS prompt")
    a = ap.parse_args(argv)

    if a.cmd == "daemon":
        from .daemon import main as dmain
        return dmain()
    if a.cmd in ("hud", "files", "settings", "edit"):
        mod = {"hud": "hud", "files": "files", "settings": "settings", "edit": "editor"}[a.cmd]
        import importlib
        m = importlib.import_module(f"jarvis.desktop.{mod}")
        return m.main([a.path] if a.cmd == "edit" and a.path else [])

    # offline-safe local operations (no daemon needed)
    from .core.config import ConfigStore, SCHEMA, apply_mode
    if a.cmd == "config":
        cfg = ConfigStore()
        if a.action == "list":
            for k in SCHEMA:
                print(f"{k:32} = {cfg.get(k)!r:12} {D}{SCHEMA[k].description}{X}")
        elif a.action == "get":
            print(json.dumps(cfg.describe(a.args[0]) if a.args and a.args[0] in SCHEMA else cfg.get(a.args[0]) if a.args else cfg.to_dict(), indent=2))
        elif a.action == "set":
            v = cfg.set(a.args[0], a.args[1], source="cli")
            print(f"{G}✔{X} {a.args[0]} = {v!r}")
            _notify_daemon("config.set", key=a.args[0], value=v)
        elif a.action == "mode":
            apply_mode(cfg, a.args[0], "cli")
            print(f"{G}✔{X} {a.args[0]} mode")
        elif a.action == "reset":
            cfg.reset(a.args[0] if a.args else None)
            print(f"{G}✔{X} reset")
        elif a.action == "export":
            cfg.export(a.args[0])
            print(f"exported to {a.args[0]}")
        elif a.action == "import":
            for w in cfg.import_(a.args[0]):
                print(f"{Y}warning:{X} {w}")
        elif a.action == "history":
            for h in cfg.history():
                print(f"{time.strftime('%F %T', time.localtime(h['ts']))} {h['source']:9} {h['key']}: {h['old']!r} → {h['new']!r}")
        elif a.action == "restore":
            cfg.restore_backup()
            print("restored latest backup")
        return 0
    if a.cmd == "provider":
        from .ai.providers import ProviderManager, TEMPLATES
        pmgr = ProviderManager()
        if a.action == "list":
            for p in pmgr.public():
                print(f"{C}{p['name']:14}{X} {p['adapter']:7} {p['base_url']:48} key={'✔' if p['has_key'] else '✘'} models={p['models']}")
            print(f"{D}templates: {', '.join(TEMPLATES)}{X}")
        elif a.action == "add":
            models = dict(m.split("=", 1) for m in a.model)
            key = getpass.getpass(f"API key for {a.name} (hidden): ") if a.key else None
            pmgr.add(a.name, a.template, key, base_url=a.base_url, models=models or None,
                     vision=a.vision or None, local=a.local_model or None, needs_key=False if a.local_model else None)
            print(f"{G}✔{X} provider {a.name} saved")
        elif a.action == "remove":
            pmgr.remove(a.name)
        elif a.action == "test":
            print(json.dumps(pmgr.test(a.name), indent=2))
        elif a.action == "model":
            for m in a.model:
                s, v = m.split("=", 1)
                pmgr.set_model(a.name, s, v)
        elif a.action == "route":   # jarvis provider route gemini --model vision
            cfg = ConfigStore()
            for slot in a.model:
                cfg.set(f"ai.route.{slot}", a.name, source="cli")
                print(f"{G}✔{X} {slot} → {a.name}")
        return 0
    if a.cmd == "mcp":
        from .mcp.client import MCPRegistry, ServerConfig
        import shlex
        reg = MCPRegistry()
        if a.action == "add":
            reg.add_server(ServerConfig(a.name, command=shlex.split(a.command) if a.command else None, url=a.url))
            print(f"{G}✔{X} added {a.name}")
        elif a.action == "remove":
            reg.remove_server(a.name)
        elif a.action in ("test", "list"):
            names = [a.name] if a.name else list(reg.servers)
            for n in names:
                try:
                    tools = reg.connect(n)
                    print(f"{G}●{X} {n}: {len(tools)} tools")
                    for t in tools:
                        print(f"   {t['name']:30} {t['op']:13} risk={t['risk']:6} {'on' if t['enabled'] else 'off'}  {D}{t['description'][:60]}{X}")
                except Exception as ex:
                    print(f"{R}●{X} {n}: {ex}")
                reg.disconnect(n)
        elif a.action in ("enable-tool", "disable-tool"):
            reg.set_tool_enabled(a.name, a.action == "enable-tool")
        return 0
    if a.cmd == "audit":
        from .core.audit import AuditLog
        log = AuditLog()
        if a.clear:
            log.clear()
            return 0
        if a.export:
            print(f"exported {log.export(a.export)} entries")
            return 0
        for e in log.entries(a.n, a.category, a.search):
            print(f"{D}{e['time']}{X} {C}{e['category']:10}{X} {e['message']}")
        return 0
    if a.cmd == "permissions":
        from .core.safety import PermissionStore
        ps = PermissionStore()
        if a.name and a.value:
            ps.set(a.name, a.value == "on")
        for k, v in ps.list().items():
            print(f"{k:18} {G+'granted' if v else R+'ask'}{X}")
        return 0
    if a.cmd == "memory":
        from .core.memory import Memory
        m = Memory()
        {"show": lambda: print(json.dumps(m.data, indent=2)), "clear": m.clear,
         "export": lambda: m.export(a.path), "import": lambda: m.import_(a.path)}[a.action]()
        return 0
    if a.cmd == "doctor":
        return doctor()

    # ---- operations that use the core (daemon or local) ----
    from .core.runtime import Client
    client = Client()
    use_local = a.local or not client.alive()
    if a.cmd == "stop":
        if not use_local:
            client.call("stop")
        print(f"{R}■ JARVIS STOPPED{X}")
        return 0
    if a.cmd == "status":
        if use_local:
            print("jarvisd not running (start with: systemctl --user start jarvisd)")
            return 1
        print(json.dumps(client.call("status"), indent=2, default=str))
        return 0
    if a.cmd == "apps":
        from .computer_control.apps import AppRegistry
        for ap_ in sorted(AppRegistry().apps.values(), key=lambda x: x.name.lower()):
            print(f"{ap_.name:36} {D}{ap_.id}{X}")
        return 0
    if a.cmd == "approvals":
        for req in client.call("approvals"):
            client.call("approve", id=req["id"], status=approval_prompt(req))
        return 0
    if a.cmd in ("do", "voice", "repl"):
        texts = []
        if a.cmd == "do":
            texts = [" ".join(a.text)]
        elif a.cmd == "voice":
            from .audio.voice import listen_once
            cfg = ConfigStore()
            print(f"{C}◉ listening…{X}")
            texts = [listen_once(cfg.get)]
            print(f"heard: {texts[0]!r}")
        if use_local:
            rt = local_runtime()
            if a.cmd == "do" and a.dry_run:
                rt.tools.dry_run = True
            run = lambda t: print(rt.command(t, wait=True)["report"])
        else:
            run = lambda t: _run_remote(client, t)
        signal.signal(signal.SIGINT, lambda *_: (client.call("stop") if not use_local else None, sys.exit(130)))
        if a.cmd == "repl":
            print(f"{C}JARVIS ready.{X} Type a command, 'stop' to halt, Ctrl-D to exit.")
            while True:
                try:
                    t = input(f"{C}jarvis ›{X} ").strip()
                except EOFError:
                    return 0
                if t:
                    run(t)
        for t in texts:
            run(t)
        return 0
    ap.print_help()
    return 0


def _notify_daemon(method, **kw):
    try:
        from .core.runtime import Client
        c = Client(timeout=2)
        c.call(method, **kw)
    except Exception:
        pass


def _run_remote(client, text):
    r = client.call("command", text=text)
    tid = r["task"]["id"]
    seen = set()
    while True:
        for req in client.call("approvals"):
            if req["id"] not in seen:
                seen.add(req["id"])
                client.call("approve", id=req["id"], status=approval_prompt(req))
        t = client.call("task", id=tid)
        if t["task"]["status"] not in ("running", "planning", "paused"):
            print(t["report"])
            return
        time.sleep(0.4)


def doctor() -> int:
    import importlib
    import shutil
    checks = [("PyAutoGUI", "pyautogui"), ("psutil", "psutil"), ("Pillow", "PIL"), ("mss", "mss"),
              ("OpenCV", "cv2"), ("MediaPipe", "mediapipe"), ("numpy", "numpy"), ("sounddevice", "sounddevice"),
              ("Vosk", "vosk"), ("Tk", "tkinter"), ("pynput", "pynput"), ("Playwright", "playwright")]
    for name, mod in checks:
        try:
            importlib.import_module(mod)
            print(f"{G}✔{X} {name}")
        except Exception as e:
            print(f"{Y}✘{X} {name:12} {D}{type(e).__name__}: {str(e)[:80]}{X}")
    for exe in ("wmctrl", "xdotool", "tesseract", "espeak-ng", "notify-send", "blender", "code", "firefox-esr", "pkexec"):
        print(f"{G+'✔' if shutil.which(exe) else Y+'✘'}{X} {exe}")
    print(f"DISPLAY={os.environ.get('DISPLAY')}  camera={'yes' if os.path.exists('/dev/video0') else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
