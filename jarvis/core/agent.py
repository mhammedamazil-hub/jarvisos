"""Long-running task agent: GOAL → PLAN → EXECUTE → OBSERVE → VERIFY → REPAIR → REPORT.

Planning uses the deterministic intent parser first; if that fails and an AI provider
is configured, the LLM produces a JSON plan constrained to the tool catalogue.
A task is only reported "completed" if every step returned ok (with verification);
otherwise it is "failed" with the reason — never a false success.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable

from . import intents
from .safety import STOP, StopRequested
from .tools import ToolExecutor, ApprovalDenied, TOOL_DOCS
from .memory import TaskStore, Memory
from .audit import AuditLog

PLANNER_SYSTEM = """You are JARVIS, the planning module of JARVISOS (Debian Linux, Openbox, X11).
Turn the user's goal into a JSON plan using ONLY these tools:
{tools}

Control hierarchy — prefer, in order: application API/scripting (blender/unreal tools with Python),
official CLI (run_command), MCP tools, browser automation, and only then gui (PyAutoGUI) or vision_task.
Batch GUI actions (move→click→type→press→wait) in ONE gui step. Add verify steps (verify_app,
run_command checks like `test -f file`, read_file) after actions that change state.
Never invent tools. Never hide commands. Use sudo=true only when truly required.
Installed applications (subset): {apps}
Known MCP tools: {mcp}

Return ONLY JSON: {{"summary": "...", "steps": [{{"tool": "...", "args": {{...}}, "description": "...", "verify": "how success is checked"}}]}}
If something required is missing (e.g. app not installed) include a step using ask_user explaining what is missing and the official install command."""

REPAIR_SYSTEM = """A step in a JARVIS plan failed. Given the goal, the failed step, the error and the recent
results, return ONLY JSON {{"action": "retry"|"replace"|"skip"|"abort", "steps": [..replacement steps..], "explanation": "..."}}.
Use the same tool catalogue: {tools}. Do not retry identical failing commands; change the approach."""


@dataclass
class Step:
    tool: str
    args: dict
    description: str = ""
    verify: str = ""
    status: str = "pending"   # pending/running/done/failed/skipped/denied
    result: dict | None = None
    attempts: int = 0


@dataclass
class Task:
    goal: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source: str = "text"
    steps: list[Step] = field(default_factory=list)
    status: str = "planning"  # planning/awaiting_approval/running/paused/completed/failed/cancelled/needs_user
    summary: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None
    planner: str = "rules"
    log: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def progress(self) -> tuple[int, int]:
        return sum(s.status in ("done", "skipped") for s in self.steps), len(self.steps)


def extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


class Agent:
    def __init__(self, tools: ToolExecutor, audit: AuditLog, memory: Memory, store: TaskStore,
                 router=None, on_update: Callable[[Task], None] | None = None):
        self.tools, self.audit, self.memory, self.store, self.router = tools, audit, memory, store, router
        self.on_update = on_update or (lambda t: None)
        self.tasks: dict[str, Task] = {}
        self.queue: list[str] = []
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.max_steps = lambda: tools.cfg.get("control.max_steps")
        self.max_retries = lambda: tools.cfg.get("control.max_retries")

    # ---------------------------------------------------------------- planning
    def plan(self, goal: str) -> Task:
        task = Task(goal=goal)
        self.audit.log("request", goal, task=task.id)
        parsed = intents.parse(goal)
        if parsed:
            task.steps = [Step(s["tool"], s["args"], s["description"]) for s in parsed]
            task.summary = "; ".join(s.description for s in task.steps)
            # AGENT-type actions returned by the parser mean "hand over to LLM"
        elif self.router:
            try:
                task.steps, task.summary = self._llm_plan(goal)
                task.planner = "llm"
            except Exception as e:
                task.status, task.error = "failed", f"planning failed: {e}"
        else:
            task.status = "failed"
            task.error = ("I couldn't map that to a built-in command and no AI provider is configured. "
                          "Add one in Settings → AI Providers (or `jarvis provider add gemini --key …`).")
        if len(task.steps) > self.max_steps():
            task.steps = task.steps[: self.max_steps()]
            task.log.append(f"plan truncated to {self.max_steps()} steps")
        self.audit.log("plan", task.summary or task.error, task=task.id, planner=task.planner,
                       steps=[{"tool": s.tool, "args": s.args} for s in task.steps])
        self.tasks[task.id] = task
        self._persist(task)
        return task

    def _llm_plan(self, goal: str) -> tuple[list[Step], str]:
        apps = ", ".join(sorted(a.name for a in self.tools.apps.apps.values())[:80])
        mcp = ", ".join(list(self.tools.mcp.tools)[:60]) if self.tools.mcp else "none"
        ctx = json.dumps(self.memory.data.get("project_context", {}))[:1500]
        msgs = [{"role": "system", "content": PLANNER_SYSTEM.format(tools="\n".join(TOOL_DOCS.values()), apps=apps, mcp=mcp)},
                {"role": "user", "content": f"Project context: {ctx}\nGoal: {goal}"}]
        out, prov = self.router.chat(msgs, slot="reasoning", json_mode=True)
        data = extract_json(out)
        steps = []
        for s in data.get("steps", []):
            if s.get("tool") not in TOOL_DOCS:
                raise ValueError(f"model proposed unknown tool {s.get('tool')}")
            steps.append(Step(s["tool"], s.get("args", {}), s.get("description", ""), s.get("verify", "")))
        if not steps:
            raise ValueError("model returned an empty plan")
        return steps, data.get("summary", "") + f" (planned by {prov})"

    # ---------------------------------------------------------------- execution
    def submit(self, goal: str, source: str = "text", background: bool = True) -> Task:
        task = self.plan(goal)
        task.source = source
        if task.status == "failed":
            self._finish(task, "failed")
            return task
        # AGENT action from gesture/clap phrases → re-plan through LLM
        if len(task.steps) == 1 and task.steps[0].tool == "agent":
            task.steps = []
        with self._lock:
            self.queue.append(task.id)
        if background:
            self._ensure_worker()
        else:
            self._drain()
        return task

    def _ensure_worker(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._drain, daemon=True, name="jarvis-agent")
        self._worker.start()

    def _drain(self):
        while True:
            with self._lock:
                if not self.queue:
                    return
                tid = self.queue.pop(0)
            self.run(self.tasks[tid])

    def run(self, task: Task) -> Task:
        STOP.reset()  # a new, explicitly requested task clears a previous emergency stop
        task.status = "running"
        self._update(task)
        executed = 0
        try:
            i = 0
            while i < len(task.steps):
                while self._pause.is_set():
                    task.status = "paused"
                    self._update(task)
                    STOP.wait(0.3)
                task.status = "running"
                STOP.check()
                executed += 1
                if executed > self.max_steps():
                    raise RuntimeError(f"step limit ({self.max_steps()}) reached — stopping to avoid a loop")
                step = task.steps[i]
                if step.status in ("done", "skipped"):
                    i += 1
                    continue
                step.status, step.attempts = "running", step.attempts + 1
                self._update(task)
                try:
                    res = self.tools.execute(step.tool, step.args, reason=f"{task.goal} — {step.description}")
                except ApprovalDenied as e:
                    step.status, step.result = "denied", {"ok": False, "error": str(e)}
                    task.error = str(e)
                    return self._finish(task, "cancelled")
                step.result = res
                if res.get("needs_user"):
                    step.status = "done"
                    task.log.append(res.get("question") or res.get("message", ""))
                    return self._finish(task, "needs_user")
                if res.get("ok"):
                    step.status = "done"
                    self.memory.append("recent_actions", {"tool": step.tool, "desc": step.description})
                    i += 1
                    continue
                # ---- failure: recover ----
                err = res.get("error") or "unknown error"
                self.audit.log("error", f"step failed: {step.description}", task=task.id, error=err)
                self.memory.append("errors", {"task": task.id, "step": step.description, "error": str(err)[:300]})
                if step.attempts <= self.max_retries() and self._transient(err):
                    task.log.append(f"retrying '{step.description}' ({err})")
                    self.audit.log("recovery", "retry", task=task.id, step=step.description)
                    STOP.wait(min(2 ** step.attempts, 8))
                    continue
                repaired = self._repair(task, i, err)
                if repaired == "skip":
                    step.status = "skipped"
                    i += 1
                    continue
                if repaired == "replaced":
                    continue
                step.status = "failed"
                task.error = f"{step.description}: {err}"
                hints = res.get("hints") or ([res["install_hint"]] if res.get("install_hint") else [])
                if hints:
                    task.error += "\nSuggestions: " + " | ".join(hints)
                return self._finish(task, "failed")
            return self._finish(task, "completed")
        except StopRequested as e:
            task.error = f"stopped: {e}"
            return self._finish(task, "cancelled")
        except Exception as e:
            task.error = f"{type(e).__name__}: {e}"
            return self._finish(task, "failed")

    @staticmethod
    def _transient(err: str) -> bool:
        return bool(re.search(r"timeout|timed out|temporar|connection|429|busy|not found on screen|window .* not found", str(err), re.I))

    def _repair(self, task: Task, i: int, err: str) -> str:
        if not self.router:
            return "none"
        repairs = sum("repair:" in l for l in task.log)
        if repairs >= 3:
            return "none"
        step = task.steps[i]
        recent = [{"tool": s.tool, "ok": (s.result or {}).get("ok"), "err": (s.result or {}).get("error")}
                  for s in task.steps[max(0, i - 3):i]]
        msgs = [{"role": "system", "content": REPAIR_SYSTEM.format(tools="\n".join(TOOL_DOCS.values()))},
                {"role": "user", "content": json.dumps({"goal": task.goal, "failed_step": asdict(step),
                                                        "error": str(err)[:1500], "recent": recent}, default=str)}]
        try:
            out, _ = self.router.chat(msgs, slot="reasoning", json_mode=True)
            d = extract_json(out)
        except Exception as e:
            task.log.append(f"repair unavailable: {e}")
            return "none"
        task.log.append(f"repair: {d.get('action')} — {d.get('explanation', '')}")
        self.audit.log("recovery", d.get("explanation", ""), task=task.id, action=d.get("action"))
        if d.get("action") == "skip":
            return "skip"
        if d.get("action") in ("replace", "retry") and d.get("steps"):
            new = [Step(s["tool"], s.get("args", {}), s.get("description", ""), s.get("verify", ""))
                   for s in d["steps"] if s.get("tool") in TOOL_DOCS]
            if new:
                step.status = "failed"
                task.steps[i:i + 1] = [step] + new
                task.steps[i].status = "skipped"
                return "replaced"
        return "none"

    def _finish(self, task: Task, status: str) -> Task:
        task.status = status
        task.finished = time.time()
        done, total = task.progress()
        self.audit.log("result", f"task {status}: {task.goal}", task=task.id, progress=f"{done}/{total}",
                       error=task.error)
        self._update(task)
        return task

    def _update(self, task: Task):
        self._persist(task)
        try:
            self.on_update(task)
        except Exception:
            pass

    def _persist(self, task: Task):
        try:
            self.store.save_task(task.to_dict())
        except Exception:
            pass

    # ---------------------------------------------------------------- control
    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def cancel(self, task_id: str | None = None):
        with self._lock:
            if task_id:
                if task_id in self.queue:
                    self.queue.remove(task_id)
                    self._finish(self.tasks[task_id], "cancelled")
                    return
            else:
                for tid in self.queue:
                    self._finish(self.tasks[tid], "cancelled")
                self.queue.clear()
        STOP.trigger("cancel")

    def resumable(self) -> list[dict]:
        return [t for t in self.store.load().values() if t["status"] in ("running", "paused")]

    def report(self, task: Task) -> str:
        done, total = task.progress()
        lines = [f"Task {task.id} — {task.status.upper()} ({done}/{total} steps)", f"Goal: {task.goal}"]
        for s in task.steps:
            mark = {"done": "✔", "failed": "✘", "skipped": "↷", "denied": "⛔", "running": "…"}.get(s.status, "·")
            lines.append(f"  {mark} {s.description or s.tool}")
            r = s.result or {}
            if r.get("error") and s.status != "done":
                lines.append(f"      error: {str(r['error'])[:300]}")
            if s.tool == "run_command" and r.get("stdout"):
                lines.append("      " + r["stdout"].strip()[-600:].replace("\n", "\n      "))
            if s.tool == "show" and r.get("data"):
                lines.append("      " + json.dumps(r["data"], indent=1)[:1500].replace("\n", "\n      "))
            for k in ("files", "path", "backup"):
                if r.get(k):
                    lines.append(f"      {k}: {r[k]}")
        if task.error:
            lines.append(f"Reason: {task.error}")
        lines += [f"Note: {l}" for l in task.log[-5:]]
        return "\n".join(lines)
