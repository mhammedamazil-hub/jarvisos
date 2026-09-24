"""Extensible AI provider manager.

Most hosted providers (OpenRouter, Mistral, Hugging Face router, Groq, Together, Ollama,
llama.cpp server, LM Studio, OpenAI) speak the OpenAI chat-completions protocol, so one
adapter covers them; Gemini gets a native adapter. New adapters register via
`register_adapter`. API keys are stored in a 0600 secrets file (or the system keyring
when `keyring` is installed) and are never logged or returned by `public()`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from ..core.config import jarvis_home
from ..core.audit import redact

TEMPLATES = {
    "gemini": {"adapter": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta",
               "models": {"text": "gemini-2.5-flash", "vision": "gemini-2.5-flash", "reasoning": "gemini-2.5-pro"},
               "vision": True},
    "openrouter": {"adapter": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "models": {"text": "openai/gpt-4o-mini"}, "vision": True},
    "mistral": {"adapter": "openai", "base_url": "https://api.mistral.ai/v1",
                "models": {"text": "mistral-small-latest", "coding": "codestral-latest", "vision": "pixtral-12b-2409"},
                "vision": True},
    "huggingface": {"adapter": "openai", "base_url": "https://router.huggingface.co/v1",
                    "models": {"text": "meta-llama/Llama-3.1-8B-Instruct"}},
    "openai": {"adapter": "openai", "base_url": "https://api.openai.com/v1", "models": {"text": "gpt-4o-mini"},
               "vision": True},
    "ollama": {"adapter": "openai", "base_url": "http://127.0.0.1:11434/v1", "models": {"text": "qwen2.5:0.5b"},
               "local": True, "needs_key": False},
    "llamacpp": {"adapter": "openai", "base_url": "http://127.0.0.1:8080/v1", "models": {"text": "local"},
                 "local": True, "needs_key": False},
}
SLOTS = ("text", "vision", "reasoning", "coding", "embedding", "fast")


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    name: str
    adapter: str = "openai"
    base_url: str = ""
    models: dict[str, str] = field(default_factory=dict)
    timeout: float = 60
    max_tokens: int = 2048
    enabled: bool = True
    local: bool = False
    vision: bool = False
    needs_key: bool = True
    cost_rank: int = 2      # 0 free/local … 5 expensive
    latency_rank: int = 2
    context: int = 32000
    headers: dict[str, str] = field(default_factory=dict)

    def model_for(self, slot: str) -> str | None:
        return self.models.get(slot) or (self.models.get("text") if slot in ("fast", "reasoning", "coding") else None) \
            or (self.models.get("text") if slot == "vision" and self.vision else None)


class SecretStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else jarvis_home() / "secrets.json"
        try:
            import keyring  # noqa
            self._kr = keyring
        except Exception:
            self._kr = None

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def set(self, name: str, secret: str) -> None:
        if self._kr and os.environ.get("JARVIS_USE_KEYRING") == "1":
            self._kr.set_password("jarvisos", name, secret)
            return
        d = self._load()
        d[name] = secret
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)

    def get(self, name: str) -> str | None:
        env = os.environ.get(f"JARVIS_KEY_{name.upper()}")
        if env:
            return env
        if self._kr and os.environ.get("JARVIS_USE_KEYRING") == "1":
            v = self._kr.get_password("jarvisos", name)
            if v:
                return v
        return self._load().get(name)

    def delete(self, name: str) -> None:
        d = self._load()
        d.pop(name, None)
        if self.path.exists():
            self.path.write_text(json.dumps(d))


def _http_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise ProviderError(f"HTTP {e.code}: {redact(body)}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"connection failed: {e}")


Message = dict  # {"role": ..., "content": str, "images": [data_url,...]}


def openai_adapter(cfg: ProviderConfig, key: str | None, model: str, messages: list[Message],
                   json_mode: bool = False, http=_http_json) -> str:
    msgs = []
    for m in messages:
        if m.get("images"):
            content = [{"type": "text", "text": m["content"]}] + \
                      [{"type": "image_url", "image_url": {"url": u}} for u in m["images"]]
        else:
            content = m["content"]
        msgs.append({"role": m["role"], "content": content})
    payload: dict[str, Any] = {"model": model, "messages": msgs, "max_tokens": cfg.max_tokens, "temperature": 0.2}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = dict(cfg.headers)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = http(cfg.base_url.rstrip("/") + "/chat/completions", payload, headers, cfg.timeout)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"unexpected response shape: {str(data)[:200]}")


def gemini_adapter(cfg: ProviderConfig, key: str | None, model: str, messages: list[Message],
                   json_mode: bool = False, http=_http_json) -> str:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        parts: list[dict] = [{"text": m["content"]}]
        for u in m.get("images") or []:
            mime, b64 = u.split(";base64,", 1)
            parts.append({"inline_data": {"mime_type": mime.replace("data:", ""), "data": b64}})
        contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    payload: dict[str, Any] = {"contents": contents,
                               "generationConfig": {"maxOutputTokens": cfg.max_tokens, "temperature": 0.2}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    url = f"{cfg.base_url.rstrip('/')}/models/{model}:generateContent"
    data = http(url, payload, {"x-goog-api-key": key or "", **cfg.headers}, cfg.timeout)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"unexpected response shape: {str(data)[:200]}")


ADAPTERS: dict[str, Callable] = {"openai": openai_adapter, "gemini": gemini_adapter}


def register_adapter(name: str, fn: Callable) -> None:
    ADAPTERS[name] = fn


class ProviderManager:
    def __init__(self, path: Path | None = None, secrets: SecretStore | None = None):
        self.path = Path(path) if path else jarvis_home() / "providers.json"
        self.secrets = secrets or SecretStore(self.path.parent / "secrets.json")
        self.providers: dict[str, ProviderConfig] = {}
        self.health: dict[str, dict] = {}
        self.load()

    def load(self):
        if self.path.exists():
            for d in json.loads(self.path.read_text()):
                self.providers[d["name"]] = ProviderConfig(**d)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(p) for p in self.providers.values()], indent=2))
        self.path.chmod(0o600)

    def add(self, name: str, template: str | None = None, api_key: str | None = None, **overrides) -> ProviderConfig:
        base = dict(TEMPLATES.get(template or name, {}))
        base.update({k: v for k, v in overrides.items() if v is not None})
        if "models" in overrides and template:
            base["models"] = {**TEMPLATES.get(template, {}).get("models", {}), **overrides["models"]}
        if not base.get("base_url"):
            raise ProviderError("base_url required for custom providers")
        if base.get("adapter", "openai") not in ADAPTERS:
            raise ProviderError(f"unknown adapter {base.get('adapter')}; available: {list(ADAPTERS)}")
        p = ProviderConfig(name=name, **base)
        self.providers[name] = p
        if api_key:
            self.secrets.set(name, api_key)
        self.save()
        return p

    def remove(self, name: str):
        self.providers.pop(name, None)
        self.secrets.delete(name)
        self.save()

    def set_model(self, name: str, slot: str, model: str):
        if slot not in SLOTS:
            raise ProviderError(f"slot must be one of {SLOTS}")
        self.providers[name].models[slot] = model
        self.save()

    def public(self) -> list[dict]:
        out = []
        for p in self.providers.values():
            d = asdict(p)
            d["has_key"] = bool(self.secrets.get(p.name)) or not p.needs_key
            d["health"] = self.health.get(p.name, {})
            d.pop("headers", None)
            out.append(d)
        return out

    def available(self, p: ProviderConfig) -> bool:
        return p.enabled and (not p.needs_key or bool(self.secrets.get(p.name)))

    def chat(self, name: str, slot: str, messages: list[Message], json_mode: bool = False, model: str | None = None) -> str:
        p = self.providers[name]
        m = model or p.model_for(slot)
        if not m:
            raise ProviderError(f"{name} has no model for slot '{slot}'")
        t = time.time()
        try:
            out = ADAPTERS[p.adapter](p, self.secrets.get(name), m, messages, json_mode)
            self.health[name] = {"ok": True, "latency": round(time.time() - t, 2), "ts": time.time()}
            return out
        except ProviderError as e:
            self.health[name] = {"ok": False, "error": str(e)[:200], "ts": time.time()}
            raise

    def test(self, name: str) -> dict:
        try:
            out = self.chat(name, "text", [{"role": "user", "content": "Reply with the single word: ready"}])
            return {"ok": True, "reply": out[:50], **self.health[name]}
        except (ProviderError, KeyError) as e:
            return {"ok": False, "error": str(e)}
