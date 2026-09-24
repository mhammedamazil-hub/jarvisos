"""Append-only JSONL audit log with secret redaction."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import jarvis_home

SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{20,})"),
    re.compile(r"(hf_[A-Za-z0-9]{20,})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"(xox[abpr]-[A-Za-z0-9\-]{10,})"),
    re.compile(r"((?:api[_-]?key|token|password|passwd|secret|authorization)\"?\s*[:=]\s*\"?)((?:Bearer\s+|Basic\s+)?[^\s\"',]+)", re.I),
    re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I),
]
SENSITIVE_KEYS = {"api_key", "apikey", "token", "password", "secret", "authorization", "access_token", "key"}

CATEGORIES = ("request", "plan", "action", "command", "file", "permission", "approval", "result", "error",
              "recovery", "config", "mcp", "gesture", "clap", "voice", "system")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for p in SECRET_PATTERNS:
            if p.groups == 2:
                out = p.sub(lambda m: m.group(1) + "***REDACTED***", out)
            else:
                out = p.sub("***REDACTED***", out)
        return out
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if str(k).lower() in SENSITIVE_KEYS and v else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class AuditLog:
    def __init__(self, path: Path | None = None, disabled_categories: set[str] | None = None, enabled: bool = True):
        self.path = Path(path) if path else jarvis_home() / "audit.jsonl"
        self.disabled = set(disabled_categories or ())
        self.enabled = enabled
        self._lock = threading.Lock()

    def log(self, category: str, message: str, **data: Any) -> dict | None:
        if not self.enabled or category in self.disabled:
            return None
        entry = {"ts": time.time(), "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "category": category,
                 "message": redact(message), **redact(data)}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists()
            with open(self.path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            if new:
                os.chmod(self.path, 0o600)
        return entry

    def entries(self, limit: int = 200, category: str | None = None, query: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if category and e.get("category") != category:
                continue
            if query and query.lower() not in line.lower():
                continue
            out.append(e)
        return out[-limit:]

    def export(self, dest: Path) -> int:
        es = self.entries(limit=10**9)
        Path(dest).write_text("\n".join(json.dumps(e) for e in es))
        return len(es)

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
