#!/usr/bin/env python3
"""Render JARVIS UI components offscreen to PNG (visual regression / docs screenshots).
Usage: QT_QPA_PLATFORM=offscreen scripts/render-ui.py OUTDIR"""
import os, sys, threading, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
out = sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"
os.makedirs(out, exist_ok=True)
os.environ.setdefault("JARVIS_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_RUNTIME_DIR", os.environ["JARVIS_HOME"])
from jarvis.core.runtime import JarvisRuntime
from jarvis.core.config import apply_mode
rt = JarvisRuntime(headless=True)
srv = rt.serve(); threading.Thread(target=srv.serve_forever, daemon=True).start()
rt.governor.step()
rt.approvals.handler = lambda r: "approved"
rt.command("switch to assisted mode", wait=True)
rt.command("When I clap twice, open VS Code", wait=True)
rt.approvals.handler = None
from PyQt5.QtWidgets import QApplication
from jarvis.desktop import theme
app = theme.ensure_app(["x"])
from jarvis.desktop.hud import HUD, ApprovalDialog, Launcher
def shot(w, name, size=(1366, 768)):
    w.resize(*size); w.show()
    for _ in range(5): app.processEvents(); time.sleep(0.05)
    w.grab().save(os.path.join(out, name)); print("saved", name)
for mode in ("performance", "cinematic"):
    apply_mode(rt.cfg, mode)
    h = HUD(); theme.apply(app, lambda k: h.cfg.get(k))
    h.poll_core(); time.sleep(1.1); rt.governor.step(); h.poll_core()
    h.reactor.t = h.wave.t = 2.0; h.wave.level = 0.8; h.reactor.state = "working" if mode == "cinematic" else "idle"
    shot(h, f"hud-{mode}.png"); h.close()
from jarvis.core.safety import assess_command
req = {"id": "x", "title": "run_command: sudo apt install blender", "reason": "Install Blender for the asset workflow",
       "assessment": assess_command("sudo apt install blender").to_dict(), "details": "$ sudo apt install blender\ncwd: /home/jarvis",
       "backup_available": False}
shot(ApprovalDialog(req), "approval.png", (760, 440))
if "--all" in sys.argv:
    from jarvis.desktop import settings, files, editor
    for mod, name in ((settings, "settings.png"), (files, "files.png"), (editor, "editor.png")):
        w = mod.build_for_screenshot()
        shot(w, name, (1100, 700))
