"""Task-aware model routing with fallback. Never pretends success on failure."""
from __future__ import annotations

import re
from typing import Callable

from .providers import ProviderManager, ProviderError, Message

CODE_HINTS = re.compile(r"\b(code|function|bug|python|javascript|compile|refactor|script|test|git|regex|api)\b", re.I)
PLAN_HINTS = re.compile(r"\b(and then|then|plan|build|create .* and|project|scene|workflow|multi|steps?)\b", re.I)


def classify(prompt: str, has_image: bool = False) -> str:
    if has_image:
        return "vision"
    if CODE_HINTS.search(prompt):
        return "coding"
    if len(prompt) > 240 or PLAN_HINTS.search(prompt):
        return "reasoning"
    return "fast"


class ModelRouter:
    def __init__(self, pm: ProviderManager, cfg_get: Callable[[str], str], online: Callable[[], bool] = lambda: True):
        self.pm = pm
        self.cfg_get = cfg_get
        self.online = online
        self.last_route: list[str] = []

    def candidates(self, slot: str) -> list[tuple[str, str]]:
        """Ordered (provider, slot) list: user preference first, then ranked fallbacks."""
        out: list[tuple[str, str]] = []
        offline = self.cfg_get("ai.offline_only") or not self.online()
        pref = self.cfg_get(f"ai.route.{slot}") or ""
        prov_pref, _, model_pref = pref.partition(":")
        if prov_pref in self.pm.providers:
            out.append((prov_pref, slot))
        ranked = sorted(self.pm.providers.values(),
                        key=lambda p: (not p.local if offline else 0,
                                       p.latency_rank if slot == "fast" else 0,
                                       -p.context if slot == "reasoning" else 0, p.cost_rank))
        for p in ranked:
            if (p.name, slot) in out:
                continue
            if offline and not p.local:
                continue
            if slot == "vision" and not (p.vision and p.model_for("vision")):
                continue
            if not p.model_for(slot):
                continue
            out.append((p.name, slot))
        return [(n, s) for n, s in out if self.pm.available(self.pm.providers[n])]

    def chat(self, messages: list[Message], slot: str | None = None, json_mode: bool = False) -> tuple[str, str]:
        if slot is None:
            last = messages[-1]
            slot = classify(last["content"], bool(last.get("images")))
        errors = []
        self.last_route = []
        for name, s in self.candidates(slot):
            pref = self.cfg_get(f"ai.route.{slot}") or ""
            model = pref.partition(":")[2] if pref.startswith(name + ":") else None
            for attempt in range(2):
                try:
                    self.last_route.append(name)
                    return self.pm.chat(name, s, messages, json_mode, model=model or None), name
                except ProviderError as e:
                    errors.append(f"{name}: {e}")
                    if "HTTP 4" in str(e) and "429" not in str(e):
                        break  # auth/bad request: don't retry same provider
        if not errors:
            raise ProviderError(f"no AI provider configured for '{slot}'. Add one with: jarvis provider add <name> --key …")
        raise ProviderError("all providers failed: " + " | ".join(errors))
