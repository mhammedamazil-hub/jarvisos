# Developer Guide

## Run the tests
```bash
pip install -r requirements-dev.txt
python3 -m pytest -q                        # 118 tests
QT_QPA_PLATFORM=offscreen python3 scripts/render-ui.py docs/screenshots --all
```
The UI tests are skipped automatically if PyQt5 can't be imported.

## Adding a tool
1. Add a `t_<name>` method to `jarvis/core/tools.py:ToolExecutor`, returning `{"ok": bool, ...}`.
   Set `ok` only after **verifying** the effect.
2. Classify its risk in `ToolExecutor.assess()`.
3. Document it in `TOOL_DOCS`. This is what the LLM planner is allowed to use.
4. Add tests.

## Adding an AI provider adapter
```python
from jarvis.ai.providers import register_adapter
def my_adapter(cfg, key, model, messages, json_mode=False, http=None) -> str: ...
register_adapter("myapi", my_adapter)
```
Then run `jarvis provider add foo --base-url ... ` with `adapter="myapi"` (via the API or `providers.json`).

## Adding an MCP server
Any stdio or streamable-HTTP MCP server works: `jarvis mcp add NAME --command "..."`.
`jarvis/mcp/servers/system_info.py` is a minimal reference server.
You can override risk per tool in `~/.config/jarvis/mcp.json` → `overrides: {"tool": {"op": "READ", "risk": "LOW"}}`.

## Daemon API
The daemon listens on a JSON-lines Unix socket at `$XDG_RUNTIME_DIR/jarvis.sock` (mode 0600).
Methods: `command, task, tasks, stop, pause, resume, status, events, config.get, config.set,
config.mode, config.structured, approvals, approve, audit, apps, launch, action, permissions,
permission.set, providers, provider.add/remove/test, mcp.list, memory, memory.clear`.

## Files (`~/.config/jarvis/`, all 0600)
`config.json`, `config-history.jsonl`, `backups/`, `audit.jsonl`, `permissions.json`, `providers.json`,
`secrets.json`, `mcp.json`, `memory.json`, `tasks.json`, `file-backups/`.

## ISO
`iso/auto/config` holds the live-build options. `iso/config/package-lists/` lists the Debian packages.
`iso/config/hooks/live/` has the chroot hooks (venv + pip deps). `iso/config/includes.chroot/` is overlaid
onto the image. `build-iso.sh` copies `jarvis/` to `/opt/jarvisos`.
