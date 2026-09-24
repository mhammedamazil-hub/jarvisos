"""UI ↔ core connection. Degrades to local config access if jarvisd is down so the
desktop never becomes unusable when the AI core crashes."""
from __future__ import annotations

from ..core.runtime import Client
from ..core.config import ConfigStore


class CoreLink:
    def __init__(self):
        self.client = Client(timeout=5)
        self.local_cfg = ConfigStore()
        self.online = False

    def call(self, method: str, **params):
        try:
            r = self.client.call(method, **params)
            self.online = True
            return r
        except OSError:
            self.online = False
            return self._fallback(method, params)

    def _fallback(self, method, p):
        c = self.local_cfg
        if method == "config.get":
            c.load()
            return c.to_dict() if not p.get("key") else c.describe(p["key"])
        if method == "config.set":
            c.set(p["key"], p["value"], source="ui-offline")
            return {"ok": True}
        if method == "config.mode":
            from ..core.config import apply_mode
            apply_mode(c, p["mode"], "ui-offline")
            return {"ok": True}
        if method == "config.structured":
            c.set_structured(p["key"], p["value"], "ui-offline")
            return {"ok": True}
        if method == "launch":
            from ..computer_control.apps import AppRegistry
            return AppRegistry(c.get("app_aliases")).launch(p["app"])
        if method == "apps":
            from ..computer_control.apps import AppRegistry
            return sorted([{"id": a.id, "name": a.name, "icon": a.icon, "categories": a.categories}
                           for a in AppRegistry().apps.values()], key=lambda a: a["name"].lower())
        if method == "status":
            from ..computer_control.system import telemetry
            return {"mode": c.get("control.mode"), "ui_mode": c.get("ui.mode"), "telemetry": telemetry(),
                    "sensors": {}, "providers": [], "mcp": {}, "offline": True, "pending_approvals": 0, "stop": False}
        if method in ("events", "approvals", "tasks", "audit"):
            return []
        if method == "providers":
            from ..ai.providers import ProviderManager
            return ProviderManager().public()
        if method == "permissions":
            from ..core.safety import PermissionStore
            return PermissionStore().list()
        if method == "permission.set":
            from ..core.safety import PermissionStore
            PermissionStore().set(p["name"], p["value"])
            return {"ok": True}
        if method == "mcp.list":
            from ..mcp.client import MCPRegistry
            r = MCPRegistry()
            return {"servers": {n: {"enabled": s.enabled, "status": "core offline"} for n, s in r.servers.items()}, "tools": {}}
        return {"ok": False, "error": "JARVIS core offline — start it with: systemctl --user restart jarvisd"}
