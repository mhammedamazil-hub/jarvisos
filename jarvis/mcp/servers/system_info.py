"""Bundled stdio MCP server exposing safe local tools (system info, notes).
Also serves as the reference implementation for custom JARVIS MCP servers."""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

NOTES = Path(os.environ.get("JARVIS_NOTES", Path.home() / ".local/share/jarvis/notes.json"))

TOOLS = [
    {"name": "get_system_info", "description": "Get OS, kernel and memory information",
     "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "list_notes", "description": "List saved notes", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_note", "description": "Create a note", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "delete_note", "description": "Delete a note by index", "inputSchema": {
        "type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]},
     "annotations": {"destructiveHint": True}},
]


def notes():
    return json.loads(NOTES.read_text()) if NOTES.exists() else []


def call(name, args):
    if name == "get_system_info":
        mem = Path("/proc/meminfo").read_text().splitlines()[:2] if Path("/proc/meminfo").exists() else []
        return f"{platform.platform()} | python {platform.python_version()} | {' '.join(mem)}"
    if name == "list_notes":
        return "\n".join(f"{i}: {n}" for i, n in enumerate(notes())) or "(no notes)"
    if name == "add_note":
        n = notes() + [args["text"]]
        NOTES.parent.mkdir(parents=True, exist_ok=True)
        NOTES.write_text(json.dumps(n))
        return f"saved note {len(n) - 1}"
    if name == "delete_note":
        n = notes()
        removed = n.pop(int(args["index"]))
        NOTES.write_text(json.dumps(n))
        return f"deleted: {removed}"
    raise KeyError(name)


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        m, p = msg.get("method"), msg.get("params", {})
        try:
            if m == "initialize":
                res = {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                       "serverInfo": {"name": "jarvis-system", "version": "0.1"}}
            elif m == "tools/list":
                res = {"tools": TOOLS}
            elif m == "tools/call":
                res = {"content": [{"type": "text", "text": call(p["name"], p.get("arguments", {}))}]}
            elif m == "ping":
                res = {}
            else:
                raise KeyError(f"method not found: {m}")
            out = {"jsonrpc": "2.0", "id": msg["id"], "result": res}
        except Exception as e:
            out = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
