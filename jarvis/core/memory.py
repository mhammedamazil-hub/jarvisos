"""Lightweight JSON memory + persistent task state."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .config import jarvis_home


class Memory:
    SECTIONS = ("preferences", "project_context", "facts", "recent_actions", "errors", "recovery")

    def __init__(self, path: Path | None = None, enabled: bool = True):
        self.path = Path(path) if path else jarvis_home() / "memory.json"
        self.enabled = enabled
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {s: ([] if s in ("recent_actions", "errors") else {}) for s in self.SECTIONS}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                pass

    def _save(self):
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str))
        self.path.chmod(0o600)

    def remember(self, section: str, key: str, value: Any) -> None:
        with self._lock:
            self.data.setdefault(section, {})[key] = value
            self._save()

    def append(self, section: str, item: Any, cap: int = 200) -> None:
        with self._lock:
            lst = self.data.setdefault(section, [])
            lst.append({"ts": time.time(), **item} if isinstance(item, dict) else item)
            del lst[:-cap]
            self._save()

    def forget(self, section: str, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self.data[section] = [] if isinstance(self.data.get(section), list) else {}
            else:
                self.data.get(section, {}).pop(key, None)
            self._save()

    def clear(self) -> None:
        with self._lock:
            self.data = {s: ([] if s in ("recent_actions", "errors") else {}) for s in self.SECTIONS}
            self._save()

    def export(self, dest: Path) -> None:
        Path(dest).write_text(json.dumps(self.data, indent=2, default=str))

    def import_(self, src: Path) -> None:
        with self._lock:
            self.data.update(json.loads(Path(src).read_text()))
            self._save()


class TaskStore:
    """Persist tasks so they survive a crash / reboot and can be resumed."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else jarvis_home() / "tasks.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {}

    def save_task(self, task: dict) -> None:
        with self._lock:
            all_ = self.load()
            all_[task["id"]] = task
            # keep last 100
            if len(all_) > 100:
                for k in sorted(all_, key=lambda k: all_[k].get("created", 0))[:-100]:
                    del all_[k]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(all_, indent=2, default=str))
