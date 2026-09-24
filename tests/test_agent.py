import json
import os
import sys
import time
from pathlib import Path

import pytest

from jarvis.core import intents
from jarvis.core.safety import STOP


# ---------------------------------------------------------------- intent parsing
@pytest.mark.parametrize("text,tool,check", [
    ("JARVIS, switch to performance mode.", "set_mode", {"mode": "performance"}),
    ("JARVIS, enable cinematic mode.", "set_mode", {"mode": "cinematic"}),
    ("JARVIS, reduce the visual effects.", "set_mode", {"mode": "performance"}),
    ("JARVIS, make the interface more futuristic.", "set_mode", {"mode": "cinematic"}),
    ("Turn off the orbital rings.", "set_setting", {"key": "ui.orbital_rings", "value": False}),
    ("Set camera FPS to 10.", "set_setting", {"key": "vision.fps", "value": 10}),
    ("Disable hand tracking.", "set_setting", {"key": "vision.hand_tracking", "value": False}),
    ("Increase clap sensitivity.", "adjust_setting", {"key": "audio.clap_sensitivity"}),
    ("Reduce false clap detections.", "adjust_setting", {"key": "audio.clap_sensitivity", "delta": -0.1}),
    ("Change the double-clap timing window to 800ms", "set_setting", {"key": "audio.clap_window_ms", "value": 800}),
    ("Move the system monitor to the left.", "set_setting", {"key": "ui.monitor_position", "value": "left"}),
    ("Make the HUD brighter.", "adjust_setting", {"key": "ui.brightness"}),
    ("Reduce transparency.", "adjust_setting", {"key": "ui.transparency"}),
    ("Make the interface darker.", "adjust_setting", {"key": "ui.darkness"}),
    ("Increase the animation speed.", "adjust_setting", {"key": "ui.animation_speed"}),
    ("Disable clap detection.", "set_setting", {"key": "audio.clap_detection", "value": False}),
    ("switch to assisted mode", "set_setting", {"key": "control.mode", "value": "assisted"}),
    ("stop", "stop", {}),
    ("open firefox", "open_app", {"app": "firefox"}),
    ("run `ls -la /tmp`", "run_command", {"command": "ls -la /tmp"}),
    ("Make pinch detection less sensitive.", "adjust_setting", {"key": "vision.pinch_threshold"}),
])
def test_intents(text, tool, check):
    steps = intents.parse(text)
    assert steps, text
    assert steps[0]["tool"] == tool
    for k, v in check.items():
        assert steps[0]["args"][k] == v


@pytest.mark.parametrize("text,gesture,action", [
    ("When I show two fingers, open VS Code.", "TWO_FINGERS", {"type": "OPEN_APPLICATION", "application": "vs code"}),
    ("When I show two fingers, open Blender.", "TWO_FINGERS", {"type": "OPEN_APPLICATION", "application": "blender"}),
    ("When I pinch, perform a left click.", "PINCH", {"type": "MOUSE", "button": "left"}),
    ("Use pinch as left click.", "PINCH", {"type": "MOUSE", "button": "left"}),
    ("When I swipe right, change workspace.", "SWIPE_RIGHT", {"type": "WINDOW", "command": "next_workspace"}),
    ("When I show an open palm, display the HUD.", "OPEN_PALM", {"type": "UI", "command": "show_hud"}),
    ("When I make a fist, hide the HUD.", "FIST", {"type": "UI", "command": "hide_hud"}),
    ("Use three fingers to open Blender.", "THREE_FINGERS", {"type": "OPEN_APPLICATION", "application": "blender"}),
])
def test_gesture_intents(text, gesture, action):
    s = intents.parse(text)[0]
    assert s["tool"] == "map_gesture" and s["args"]["gesture"] == gesture and s["args"]["action"] == action


@pytest.mark.parametrize("text,count,app", [
    ("When I clap once, open Discord.", 1, "discord"),
    ("When I clap twice, open VS Code.", 2, "vs code"),
    ("When I clap three times, open Blender.", 3, "blender"),
])
def test_clap_intents(text, count, app):
    s = intents.parse(text)[0]
    assert s["tool"] == "map_clap" and s["args"]["count"] == count
    assert s["args"]["action"]["application"] == app


def test_disable_gesture_intent():
    s = intents.parse("Disable thumbs-down detection.")[0]
    assert s["tool"] == "toggle_gesture" and s["args"] == {"gesture": "THUMBS_DOWN", "enabled": False}


def test_unknown_goes_to_llm():
    assert intents.parse("write me a poem about linux and then email it to bob") is None


# ---------------------------------------------------------------- runtime / agent
def test_ai_changes_settings_end_to_end(runtime):
    r = runtime.command("JARVIS, enable cinematic mode.", wait=True)
    assert r["task"]["status"] == "completed", r["report"]
    assert runtime.cfg.get("ui.mode") == "cinematic"
    r = runtime.command("When I clap twice, open VS Code.", wait=True)
    assert r["task"]["status"] == "completed", r["report"]
    assert next(c for c in runtime.cfg.get("claps") if c["count"] == 2)["action"]["application"] == "vs code"
    assert runtime.cfg.get("audio.clap_detection") is True
    r = runtime.command("When I show two fingers, open Blender.", wait=True)
    assert r["task"]["status"] == "completed"
    assert any(g["gesture"] == "TWO_FINGERS" for g in runtime.cfg.get("gestures"))
    # config persisted on disk
    data = json.loads((Path(os.environ["JARVIS_HOME"]) / "config.json").read_text())
    assert data["settings"]["ui.mode"] == "cinematic"
    # audit trail recorded
    cats = {e["category"] for e in runtime.audit.entries()}
    assert {"request", "plan", "result", "permission"} <= cats
    runtime.shutdown()


def test_control_mode_escalation_requires_approval(runtime):
    runtime._answers = ["denied"]
    r = runtime.command("switch to full control mode", wait=True)
    assert r["task"]["status"] == "cancelled"
    assert runtime.cfg.get("control.mode") == "safe"
    runtime._answers = ["approved"]
    r = runtime.command("switch to full control mode", wait=True)
    assert runtime.cfg.get("control.mode") == "full"
    runtime.shutdown()


def test_command_needs_approval_in_safe_mode(runtime, tmp_path):
    runtime._answers = ["denied"]
    r = runtime.command("run `echo hi`", wait=True)
    assert r["task"]["status"] == "cancelled"
    runtime._answers = ["approved"]
    r = runtime.command("run `echo hi`", wait=True)
    assert r["task"]["status"] == "completed" and "hi" in r["report"]
    runtime.shutdown()


def test_critical_always_asks_even_in_full(runtime):
    runtime.cfg.set("control.mode", "full")
    seen = []
    runtime.approvals.handler = lambda req: seen.append(req) or "denied"
    r = runtime.command("run `sudo mkfs.ext4 /dev/sda1`", wait=True)
    assert r["task"]["status"] == "cancelled" and seen and seen[0].assessment["risk"] == "CRITICAL"
    runtime.shutdown()


def test_failed_command_not_reported_success(runtime):
    runtime.cfg.set("control.mode", "assisted")
    runtime._answers = ["approved"] * 5
    r = runtime.command("run `ls /definitely/not/here`", wait=True)
    assert r["task"]["status"] == "failed"
    assert "No such file" in r["report"] or "exit code" in r["report"]
    runtime.shutdown()


def test_root_file_edit_requires_approval_and_backup(runtime, tmp_path):
    runtime.cfg.set("control.mode", "full")
    seen = []
    runtime.approvals.handler = lambda req: seen.append(req) or "denied"
    from jarvis.core.tools import ApprovalDenied
    with pytest.raises(ApprovalDenied):
        runtime.tools.execute("write_file", {"path": "/etc/jarvis-test.conf", "content": "x=1"})
    assert seen[0].assessment["risk"] == "HIGH" and seen[0].backup_available
    # user file: backup + verification + undo
    f = tmp_path / "note.txt"
    f.write_text("old")
    runtime.approvals.handler = lambda req: "approved"
    res = runtime.tools.execute("write_file", {"path": str(f), "content": "new"})
    assert res["ok"] and res["verified"] and Path(res["backup"]).read_text() == "old"
    runtime.shutdown()


def test_file_ops_trash_undo(runtime, tmp_path):
    runtime.approvals.handler = lambda req: "approved"
    f = tmp_path / "doomed.txt"
    f.write_text("bye")
    res = runtime.tools.execute("delete_file", {"path": str(f)})
    assert res["ok"] and not f.exists() and Path(res["trashed"]).read_text() == "bye"
    u = res["undo"]
    runtime.tools.execute("move_file", {"src": u["src"], "dst": u["dst"]})
    assert f.read_text() == "bye"
    res = runtime.tools.execute("mkdir", {"path": str(tmp_path / "a" / "b")})
    assert res["ok"] and (tmp_path / "a" / "b").is_dir()
    runtime.shutdown()


def test_emergency_stop_cancels_task(runtime):
    import threading
    runtime.cfg.set("control.mode", "full")
    runtime.permissions.set("input_control", True)
    runtime.approvals.handler = lambda req: "approved"
    threading.Timer(0.3, STOP.trigger, args=("test",)).start()
    t0 = time.time()
    task = runtime.agent.submit("x", background=False) if False else None
    from jarvis.core.agent import Task, Step
    task = Task(goal="long gui task", steps=[Step("gui", {"actions": [{"type": "wait", "seconds": 20}]}, "wait"),
                                            Step("gui", {"actions": [{"type": "click", "x": 5, "y": 5}]}, "click")])
    runtime.agent.run(task)
    assert task.status == "cancelled" and time.time() - t0 < 3
    assert not any(c[0] == "click" for c in runtime._fake_gui.calls)
    runtime.shutdown()


def test_step_limit_prevents_infinite_loop(runtime):
    runtime.cfg.set("control.max_steps", 3)
    from jarvis.core.agent import Task, Step
    runtime.approvals.handler = lambda req: "approved"
    task = Task(goal="many", steps=[Step("show", {"what": "telemetry"}, f"s{i}") for i in range(10)])
    runtime.agent.run(task)
    assert task.status == "failed" and "step limit" in task.error
    runtime.shutdown()


def test_no_provider_fallback_message(runtime):
    r = runtime.command("compose a haiku about kernels and save it somewhere nice", wait=True)
    assert r["task"]["status"] == "failed" and "no AI provider" in r["report"]
    runtime.shutdown()


def test_llm_planner_and_repair(runtime, tmp_path):
    """Fake provider: plan has a failing command; repair replaces it; verified completion."""
    target = tmp_path / "proj"
    plans = [
        json.dumps({"summary": "make project", "steps": [
            {"tool": "run_command", "args": {"command": "false"}, "description": "broken step"}]}),
        json.dumps({"action": "replace", "explanation": "use mkdir", "steps": [
            {"tool": "mkdir", "args": {"path": str(target)}, "description": "create dir"}]}),
    ]
    from jarvis.ai import providers as P
    P.register_adapter("fake", lambda cfg, key, model, msgs, json_mode=False, http=None: plans.pop(0))
    runtime.providers.add("fakeai", adapter="fake", base_url="http://none", models={"text": "m"}, needs_key=False)
    runtime.approvals.handler = lambda req: "approved"
    r = runtime.command("set up a new project folder for me please", wait=True)
    assert r["task"]["status"] == "completed", r["report"]
    assert target.is_dir()
    runtime.shutdown()


def test_blender_unreal_recipe_reports_missing(runtime):
    steps = intents.parse("Open Blender, create assets for realistic Minecraft, and build a scene with Unreal Engine.")
    tools = [s["tool"] for s in steps]
    assert tools[0] == "blender" and "ask_user" in tools
    runtime.approvals.handler = lambda req: "approved"
    r = runtime.command("Open Blender, create assets for realistic Minecraft, and build a scene with Unreal Engine.", wait=True)
    # Blender isn't installed in CI: must fail honestly with an install hint, not claim success
    import shutil
    if not shutil.which("blender"):
        assert r["task"]["status"] == "failed" and "apt install blender" in r["report"]
    runtime.shutdown()


def test_gesture_and_clap_route_through_actions(runtime, tmp_path):
    from jarvis.vision.gestures import GestureEvent
    from jarvis.audio.clap import ClapEvent
    runtime.cfg.set("control.mode", "assisted")
    runtime.cfg.upsert_gesture({"gesture": "FIST", "action": {"type": "UI", "command": "hide_hud"}})
    runtime.on_gesture(GestureEvent("FIST", 0.9, 0))
    assert runtime.cfg.get("ui.overlay_visible") is False
    runtime.cfg.upsert_clap({"count": 1, "action": {"type": "OPEN_APPLICATION", "application": "zzz-not-installed"}})
    launched = []
    runtime.apps.launch = lambda q, dry_run=False: launched.append(q) or {"ok": True}
    runtime.on_clap(ClapEvent(1, 0, 0.9))
    assert launched == ["zzz-not-installed"]
    # a gesture mapped to a shell command must go through approval
    runtime.cfg.upsert_gesture({"gesture": "THUMBS_DOWN", "action": {"type": "COMMAND", "command": "rm -rf ~/x"}})
    asked = []
    runtime.approvals.handler = lambda req: asked.append(req) or "denied"
    res = runtime.on_gesture(GestureEvent("THUMBS_DOWN", 0.95, 0))
    assert asked
    runtime.shutdown()


def test_resource_governor_degrades(runtime):
    from jarvis.core.config import apply_mode
    apply_mode(runtime.cfg, "cinematic")
    runtime.governor.step({"cpu": 99, "ram_avail_mb": 100})
    assert runtime.governor.load > 0.8 and runtime.cfg.get("ui.mode") == "performance"
    runtime.governor.step({"cpu": 5, "ram_avail_mb": 3000})
    assert runtime.governor.load == 0
    runtime.shutdown()


def test_socket_api(runtime):
    import threading
    from jarvis.core.runtime import Client
    srv = runtime.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client()
    assert c.call("status")["mode"] == "safe"
    r = c.call("command", text="set camera fps to 12")
    for _ in range(50):
        t = c.call("task", id=r["task"]["id"])
        if t["task"]["status"] == "completed":
            break
        time.sleep(0.05)
    assert runtime.cfg.get("vision.fps") == 12
    assert any(e["category"] == "request" for e in c.call("audit"))
    srv.shutdown()
    runtime.shutdown()
