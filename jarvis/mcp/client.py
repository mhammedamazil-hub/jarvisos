"""Minimal Model Context Protocol client (JSON-RPC 2.0 over stdio or streamable HTTP)
and a tool registry that attaches risk metadata for the approval system."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..core.config import jarvis_home
from ..core.safety import Assessment, OpType, Risk

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


class StdioTransport:
    def __init__(self, command: list[str], env: dict | None = None, cwd: str | None = None):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, env={**os.environ, **(env or {})}, cwd=cwd)
        self._lock = threading.Lock()

    def request(self, msg: dict, timeout: float = 30) -> dict | None:
        with self._lock:
            if self.proc.poll() is not None:
                raise MCPError(f"server exited ({self.proc.returncode}): {self.proc.stderr.read()[:300]}")
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            if "id" not in msg:
                return None
            result: list = []
            def reader():
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    try:
                        resp = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if resp.get("id") == msg["id"]:
                        result.append(resp)
                        return
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            t.join(timeout)
            if not result:
                raise MCPError(f"timeout waiting for {msg.get('method')}")
            return result[0]

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class HTTPTransport:
    def __init__(self, url: str, headers: dict | None = None):
        self.url, self.headers, self.session = url, headers or {}, None

    def request(self, msg: dict, timeout: float = 30) -> dict | None:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.session = r.headers.get("Mcp-Session-Id", self.session)
            body = r.read().decode()
        if "id" not in msg:
            return None
        if body.lstrip().startswith("{"):
            return json.loads(body)
        for line in body.splitlines():  # SSE
            if line.startswith("data:"):
                d = json.loads(line[5:])
                if d.get("id") == msg["id"]:
                    return d
        raise MCPError("no response")

    def close(self):
        pass


class MCPClient:
    def __init__(self, transport):
        self.t = transport
        self._id = 0
        self.server_info: dict = {}

    def _call(self, method: str, params: dict | None = None, timeout: float = 30) -> Any:
        self._id += 1
        resp = self.t.request({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}, timeout)
        if "error" in resp:
            raise MCPError(f"{method}: {resp['error'].get('message')}")
        return resp.get("result")

    def initialize(self) -> dict:
        r = self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": {"name": "jarvisos", "version": "0.1"}})
        self.server_info = r or {}
        self.t.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.server_info

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            r = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools += r.get("tools", [])
            cursor = r.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict, timeout: float = 60) -> dict:
        return self._call("tools/call", {"name": name, "arguments": arguments}, timeout)

    def close(self):
        self.t.close()


# ---------------------------------------------------------------- registry
OP_RULES = [
    (r"(delete|remove|destroy|drop|purge|trash)", OpType.DELETE, Risk.HIGH),
    (r"(send|post|publish|reply|email|message|share|invite|comment|tweet)", OpType.SEND, Risk.HIGH),
    (r"(exec|run|shell|command|eval|deploy|execute)", OpType.EXECUTE, Risk.HIGH),
    (r"(install|config|permission|admin|system)", OpType.SYSTEM_CHANGE, Risk.HIGH),
    (r"(create|update|write|edit|set|add|insert|move|rename|upload|append|push|merge)", OpType.WRITE, Risk.MEDIUM),
    (r"(get|list|read|search|find|query|fetch|view|describe|show)", OpType.READ, Risk.LOW),
]


def classify_tool(tool: dict) -> tuple[OpType, Risk]:
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint"):
        return OpType.DELETE, Risk.HIGH
    if ann.get("readOnlyHint"):
        return OpType.READ, Risk.LOW
    text = f"{tool.get('name','')}"
    for pat, op, risk in OP_RULES:
        if re.search(pat, text, re.I):
            return op, risk
    text = tool.get("description", "")
    for pat, op, risk in OP_RULES:
        if re.search(pat, text, re.I):
            return op, risk
    return OpType.EXECUTE, Risk.MEDIUM


@dataclass
class ServerConfig:
    name: str
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    disabled_tools: list[str] = field(default_factory=list)
    overrides: dict[str, dict] = field(default_factory=dict)   # tool -> {"op":..,"risk":..}


class MCPRegistry:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else jarvis_home() / "mcp.json"
        self.servers: dict[str, ServerConfig] = {}
        self.clients: dict[str, MCPClient] = {}
        self.tools: dict[str, dict] = {}   # "server.tool" -> metadata
        self.status: dict[str, str] = {}
        if self.path.exists():
            for d in json.loads(self.path.read_text()).get("servers", []):
                self.servers[d["name"]] = ServerConfig(**d)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"servers": [asdict(s) for s in self.servers.values()]}, indent=2))
        self.path.chmod(0o600)

    def add_server(self, cfg: ServerConfig):
        if not (cfg.command or cfg.url):
            raise MCPError("server needs command or url")
        self.servers[cfg.name] = cfg
        self.save()

    def remove_server(self, name: str):
        self.disconnect(name)
        self.servers.pop(name, None)
        self.tools = {k: v for k, v in self.tools.items() if v["server"] != name}
        self.save()

    def connect(self, name: str) -> list[dict]:
        cfg = self.servers[name]
        if not cfg.enabled:
            raise MCPError(f"{name} is disabled")
        self.disconnect(name)
        t = StdioTransport(cfg.command, cfg.env) if cfg.command else HTTPTransport(cfg.url, cfg.env)
        c = MCPClient(t)
        try:
            c.initialize()
            tools = c.list_tools()
        except Exception as e:
            c.close()
            self.status[name] = f"error: {e}"
            raise MCPError(f"{name}: {e}")
        self.clients[name] = c
        self.status[name] = "connected"
        for tool in tools:
            op, risk = classify_tool(tool)
            ov = cfg.overrides.get(tool["name"], {})
            op = OpType(ov.get("op", op.value))
            risk = Risk[ov.get("risk", risk.name)]
            self.tools[f"{name}.{tool['name']}"] = {
                "server": name, "name": tool["name"], "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {}), "op": op.value, "risk": risk.name,
                "permissions": ["mcp"] + (["send_messages", "external_comm"] if op == OpType.SEND else []),
                "enabled": tool["name"] not in cfg.disabled_tools,
                "approval": "always" if risk >= Risk.HIGH else "policy"}
        return [t for t in self.tools.values() if t["server"] == name]

    def connect_all(self) -> dict[str, str]:
        for n, s in self.servers.items():
            if s.enabled:
                try:
                    self.connect(n)
                except MCPError:
                    pass
        return dict(self.status)

    def disconnect(self, name: str):
        c = self.clients.pop(name, None)
        if c:
            c.close()
        self.status[name] = "disconnected"

    def set_tool_enabled(self, fq: str, enabled: bool):
        server, _, tool = fq.partition(".")
        cfg = self.servers[server]
        s = set(cfg.disabled_tools)
        (s.discard if enabled else s.add)(tool)
        cfg.disabled_tools = sorted(s)
        if fq in self.tools:
            self.tools[fq]["enabled"] = enabled
        self.save()

    def assessment(self, fq: str) -> Assessment:
        t = self.tools[fq]
        return Assessment(Risk[t["risk"]], OpType(t["op"]), t["permissions"], [f"MCP {fq}: {t['op']}"],
                          reversible=t["op"] in ("READ", "WRITE"), critical=t["op"] == "DELETE")

    def call(self, fq: str, args: dict) -> dict:
        t = self.tools.get(fq)
        if not t:
            raise MCPError(f"unknown tool {fq}")
        if not t["enabled"]:
            raise MCPError(f"tool {fq} is disabled")
        req = t["parameters"].get("required", []) if isinstance(t["parameters"], dict) else []
        missing = [r for r in req if r not in args]
        if missing:
            raise MCPError(f"missing arguments: {missing}")
        c = self.clients.get(t["server"]) or (self.connect(t["server"]) and self.clients[t["server"]])
        r = c.call_tool(t["name"], args)
        text = "\n".join(p.get("text", "") for p in r.get("content", []) if p.get("type") == "text")
        return {"ok": not r.get("isError"), "text": text, "raw": r}
