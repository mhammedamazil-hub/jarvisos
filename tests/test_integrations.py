import json
import sys
from pathlib import Path

import pytest

from jarvis.ai.providers import ProviderManager, ProviderError, openai_adapter, gemini_adapter, ProviderConfig
from jarvis.ai.router import ModelRouter, classify
from jarvis.mcp.client import MCPRegistry, ServerConfig, MCPError, classify_tool
from jarvis.computer_control.apps import AppRegistry, parse_desktop_file
from jarvis.core.safety import OpType, Risk


# ---------------------------------------------------------------- providers
def test_provider_templates_and_secret_storage(tmp_path):
    pm = ProviderManager(tmp_path / "p.json")
    pm.add("mistral", api_key="sk-mistral-secret-123456789")
    pm.add("myendpoint", adapter="openai", base_url="https://llm.example/v1", models={"text": "foo", "coding": "bar"},
           api_key="k")
    pub = json.dumps(pm.public())
    assert "sk-mistral-secret" not in pub and "codestral" in pub
    assert (tmp_path / "secrets.json").stat().st_mode & 0o077 == 0
    assert ProviderManager(tmp_path / "p.json").secrets.get("mistral") == "sk-mistral-secret-123456789"
    with pytest.raises(ProviderError):
        pm.add("broken")  # no template, no base_url
    pm.set_model("myendpoint", "vision", "llava")
    assert pm.providers["myendpoint"].models["vision"] == "llava"


def test_openai_and_gemini_payloads():
    seen = {}
    def http(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers)
        if "generateContent" in url:
            return {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        return {"choices": [{"message": {"content": "hello"}}]}
    cfg = ProviderConfig("x", base_url="https://api.x/v1/")
    msgs = [{"role": "user", "content": "look", "images": ["data:image/jpeg;base64,AAAA"]}]
    assert openai_adapter(cfg, "k", "m", msgs, True, http=http) == "hello"
    assert seen["url"] == "https://api.x/v1/chat/completions" and seen["headers"]["Authorization"] == "Bearer k"
    assert seen["payload"]["messages"][0]["content"][1]["type"] == "image_url"
    g = ProviderConfig("g", adapter="gemini", base_url="https://gl/v1beta")
    assert gemini_adapter(g, "k", "gem", [{"role": "system", "content": "sys"}] + msgs, http=http) == "hi"
    assert seen["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["payload"]["contents"][0]["parts"][1]["inline_data"]["data"] == "AAAA"


def test_router_fallback_and_preferences(tmp_path):
    from jarvis.ai import providers as P
    calls = []
    def a_fail(cfg, key, model, msgs, json_mode=False, http=None):
        calls.append(cfg.name)
        raise ProviderError("connection failed: down")
    def a_ok(cfg, key, model, msgs, json_mode=False, http=None):
        calls.append(cfg.name)
        return f"{cfg.name}:{model}"
    P.register_adapter("afail", a_fail)
    P.register_adapter("aok", a_ok)
    pm = ProviderManager(tmp_path / "p.json")
    pm.add("primary", adapter="afail", base_url="x", models={"text": "t"}, needs_key=False)
    pm.add("backup", adapter="aok", base_url="x", models={"text": "t2", "coding": "c2"}, needs_key=False)
    prefs = {"ai.route.coding": "primary", "ai.offline_only": False}
    r = ModelRouter(pm, lambda k: prefs.get(k, ""))
    out, prov = r.chat([{"role": "user", "content": "fix this python function"}])
    assert prov == "backup" and out == "backup:c2" and calls[0] == "primary"
    prefs["ai.offline_only"] = True
    with pytest.raises(ProviderError, match="no AI provider"):
        r.chat([{"role": "user", "content": "hi"}])
    assert classify("x", True) == "vision" and classify("open firefox") == "fast"


# ---------------------------------------------------------------- MCP
def test_mcp_stdio_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_NOTES", str(tmp_path / "notes.json"))
    reg = MCPRegistry(tmp_path / "mcp.json")
    reg.add_server(ServerConfig("sys", command=[sys.executable, "-m", "jarvis.mcp.servers.system_info"],
                                env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])}))
    tools = {t["name"]: t for t in reg.connect("sys")}
    assert tools["get_system_info"]["op"] == "READ"
    assert tools["delete_note"]["op"] == "DELETE" and tools["delete_note"]["risk"] == "HIGH"
    assert tools["add_note"]["op"] == "WRITE"
    assert reg.assessment("sys.delete_note").critical
    assert reg.call("sys.add_note", {"text": "buy milk"})["ok"]
    assert "buy milk" in reg.call("sys.list_notes", {})["text"]
    with pytest.raises(MCPError, match="missing"):
        reg.call("sys.add_note", {})
    reg.set_tool_enabled("sys.add_note", False)
    with pytest.raises(MCPError, match="disabled"):
        reg.call("sys.add_note", {"text": "x"})
    assert MCPRegistry(tmp_path / "mcp.json").servers["sys"].disabled_tools == ["add_note"]
    reg.remove_server("sys")
    assert not reg.tools


def test_mcp_tool_classification():
    assert classify_tool({"name": "gmail_send_message"})[0] == OpType.SEND
    assert classify_tool({"name": "calendar_list_events"})[0] == OpType.READ
    assert classify_tool({"name": "sheets_update_cell"})[0] == OpType.WRITE
    assert classify_tool({"name": "run_query", "annotations": {"readOnlyHint": True}})[0] == OpType.READ


def test_mcp_bad_server(tmp_path):
    reg = MCPRegistry(tmp_path / "m.json")
    reg.add_server(ServerConfig("bad", command=[sys.executable, "-c", "import sys; sys.exit(1)"]))
    with pytest.raises(MCPError):
        reg.connect("bad")
    assert reg.status["bad"].startswith("error")


# ---------------------------------------------------------------- apps
def test_app_discovery(tmp_path):
    d = tmp_path / "share" / "applications"
    d.mkdir(parents=True)
    (d / "code.desktop").write_text("[Desktop Entry]\nType=Application\nName=Visual Studio Code\nExec=/usr/bin/code %F\nKeywords=vscode;editor\n")
    (d / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden\nExec=x\nNoDisplay=true\n")
    (d / "blender.desktop").write_text("[Desktop Entry]\nType=Application\nName=Blender\nExec=blender %f\nTerminal=false\n")
    (d / "broken.desktop").write_text("garbage\x00")
    reg = AppRegistry({"vs code": "code"}, dirs=[tmp_path / "share"])
    assert set(reg.apps) == {"code", "blender"}
    assert reg.resolve("VS Code").id == "code"
    assert reg.resolve("blender").argv() == ["blender"]
    assert reg.resolve("visual studio").id == "code"
    assert reg.launch("vs code", dry_run=True)["argv"] == ["/usr/bin/code"]
    r = reg.launch("discord")
    assert not r["ok"] and "discord.com" in r["install_hint"]


def test_screen_unavailable_fallback(runtime):
    runtime.approvals.handler = lambda req: "approved"
    r = runtime.tools.execute("screen", {"question": "what?"})
    assert r["ok"] is False and "screenshot unavailable" in r["error"]
    runtime.shutdown()


def test_camera_and_mic_unavailable_do_not_crash(runtime):
    runtime.cfg.set("vision.hand_tracking", True)
    runtime.cfg.set("audio.clap_detection", True)
    import time
    for _ in range(40):
        if runtime.sensor_status["hand_tracking"] not in ("off",) and runtime.sensor_status["clap"] not in ("off",):
            break
        time.sleep(0.05)
    assert runtime.sensor_status["hand_tracking"].startswith(("error", "unavailable", "running"))
    assert runtime.sensor_status["clap"].startswith(("error", "unavailable", "running"))
    # core still works
    r = runtime.command("set camera fps to 5", wait=True)
    assert r["task"]["status"] == "completed"
    runtime.shutdown()
