"""Vision-guided GUI loop: SCREENSHOT → VISION MODEL → ACTION BATCH → VALIDATE →
APPROVE → EXECUTE → SCREENSHOT → VERIFY. One model call per batch, never per mouse move."""
from __future__ import annotations

import json

from .safety import STOP
from ..computer_control.actions import validate_batch, screen_size, ActionError

VISION_SYSTEM = """You control a Linux desktop via PyAutoGUI. Screen is {w}x{h} pixels; the screenshot
is downscaled by {scale} so multiply coordinates you read by {inv:.3f}. Active window: {win}.
OCR words with full-screen centre coordinates: {ocr}
Goal: {goal}
Previous rounds: {history}
Return ONLY JSON: {{"observation": "...", "done": bool, "success_evidence": "what on screen proves the goal is met",
"actions": [ ... up to 12 actions of types move, click, double_click, right_click, drag, scroll, type, press,
hotkey, wait, wait_window ... ]}}
Set done=true only if the screenshot clearly shows the goal achieved. Prefer keyboard shortcuts."""


def run_vision_task(ex, goal: str, max_rounds: int = 8) -> dict:
    from ..vision import screen
    from ..computer_control.system import active_window
    if not ex.router:
        return {"ok": False, "error": "vision control needs a vision-capable AI provider"}
    if not ex.gui:
        return {"ok": False, "error": "GUI control unavailable"}
    scale = ex.cfg.get("vision.screenshot_scale")
    w, h = screen_size()
    history: list[dict] = []
    for rnd in range(max_rounds):
        STOP.check()
        img = screen.capture(scale)
        if img is None:
            return {"ok": False, "error": "cannot capture screen"}
        boxes = screen.ocr_boxes(img)[:120]
        prompt = VISION_SYSTEM.format(w=w, h=h, scale=scale, inv=1 / scale, win=active_window().get("title"),
                                      ocr=json.dumps([[b["text"], b["x"], b["y"]] for b in boxes]),
                                      goal=goal, history=json.dumps(history[-4:]))
        try:
            out, prov = ex.router.chat([{"role": "user", "content": prompt, "images": [screen.to_data_url(img)]}],
                                       slot="vision", json_mode=True)
            from .agent import extract_json
            d = extract_json(out)
        except Exception as e:
            return {"ok": False, "error": f"vision model failed: {e}"}
        if d.get("done"):
            return {"ok": True, "rounds": rnd + 1, "evidence": d.get("success_evidence", ""), "provider": prov}
        try:
            actions = validate_batch({"actions": d.get("actions", [])}, (w, h))
        except ActionError as e:
            history.append({"round": rnd, "invalid_batch": str(e)})
            continue
        ex.gate("gui", {"actions": actions}, reason=f"{goal}: {d.get('observation', '')}")
        res = ex.gui.execute({"actions": actions}, retries=1)
        history.append({"round": rnd, "observation": d.get("observation"),
                        "executed": len([r for r in res if r.ok]), "errors": [r.error for r in res if not r.ok]})
        STOP.wait(0.8)
    return {"ok": False, "error": f"goal not verified after {max_rounds} rounds", "history": history}
