"""Pluggable LLM clients (Part C.6). OPTIONAL: the rule-based core needs no LLM.

Providers: ollama (local, default privacy-preserving), gemini (cloud; blocked while LLM_LOCAL_ONLY=true),
scripted (fixtures for tests). No silent fallback: failures raise LLMError (Rule 6).
NOTE: the Ollama/Gemini HTTP paths are unit-tested with mocked HTTP only; they were NOT exercised against
live services in this build.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from src.config import Settings, get_settings
from src.safety.phi import find_phi


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    name = "llm"

    @abstractmethod
    def generate(self, prompt: str, system: Optional[str] = None) -> str: ...

    def _guard(self, *texts: str) -> None:
        found = find_phi([t for t in texts if t])
        if found:
            raise LLMError(f"Refusing to send text containing possible PHI ({', '.join(found)}) to an LLM")


def _post(url: str, body: dict, timeout: float = 60.0, headers: Optional[Dict[str, str]] = None) -> dict:
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise LLMError(f"LLM request failed: {e}") from e


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, host: str, model: str, temperature: float = 0.1, seed: int = 42):
        host = host.rstrip("/")
        if "0.0.0.0" in host:
            host = host.replace("0.0.0.0", "127.0.0.1")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        if ":11434" not in host and not any(host.endswith(f":{p}") for p in range(1000, 65536)):
            host = host + ":11434"
        self.host, self.model, self.temperature, self.seed = host, model, temperature, seed

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self._guard(prompt, system or "")
        body = {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": self.temperature, "seed": self.seed}}
        if system:
            body["system"] = system
        out = _post(f"{self.host}/api/generate", body)
        if "response" not in out:
            raise LLMError("Unexpected Ollama response")
        return out["response"]


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float = 0.1, seed: int = 42):
        if not api_key or not model:
            raise LLMError("GEMINI_API_KEY and GEMINI_MODEL must both be set (model ids are retired regularly; check the current list)")
        self.api_key, self.model, self.temperature, self.seed = api_key, model, temperature, seed

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self._guard(prompt, system or "")
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": self.temperature, "seed": self.seed}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        out = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}", body)
        try:
            return out["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError("Unexpected Gemini response") from e


class AnthropicClient(LLMClient):
    """Cloud provider (blocked while LLM_LOCAL_ONLY=true, same as gemini - it leaves the machine).
    Uses api.anthropic.com/v1/messages directly (no SDK dependency, consistent with the other clients
    in this file). Paid per-token, no free tier (see docs.anthropic.com/en/docs/about-claude/pricing) -
    prefer OllamaClient (free, local) or GeminiClient (free tier) unless you specifically want this one.
    Like the other two, its HTTP path here is unit-tested with mocked transport only; it was NOT run
    against the live API while building this project."""
    name = "anthropic"

    def __init__(self, api_key: str, model: str, temperature: float = 0.1, seed: int = 42):
        if not api_key or not model:
            raise LLMError("ANTHROPIC_API_KEY and ANTHROPIC_MODEL must both be set")
        self.api_key, self.model, self.temperature, self.seed = api_key, model, temperature, seed

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self._guard(prompt, system or "")
        body = {"model": self.model, "max_tokens": 512, "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        out = _post("https://api.anthropic.com/v1/messages", body,
                     headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        try:
            parts = [b["text"] for b in out["content"] if b.get("type") == "text"]
            if not parts:
                raise KeyError("no text block")
            return "".join(parts)
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected Anthropic response: {out}") from e


class ScriptedLLMClient(LLMClient):
    """Deterministic fixture client: returns queued responses in order; records prompts."""
    name = "scripted"

    def __init__(self, responses: List[str]):
        self.responses, self.prompts = list(responses), []

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise LLMError("ScriptedLLMClient exhausted")
        return self.responses.pop(0)

class FallbackLLMClient(LLMClient):
    """Tries primary; on LLMError (e.g. Ollama unreachable), tries fallback. Exposes which provider
    actually served the LAST call via .last_used, and reports both configured names via .name, so
    /health and audit logging can show the real state instead of just 'fallback'."""

    def __init__(self, primary: LLMClient, fallback: LLMClient):
        self.primary = primary
        self.fallback = fallback
        self.last_used = primary.name

    @property
    def name(self) -> str:
        return f"{self.primary.name}(fallback:{self.fallback.name})"

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        try:
            out = self.primary.generate(prompt, system)
            self.last_used = self.primary.name
            return out
        except LLMError as primary_error:
            try:
                out = self.fallback.generate(prompt, system)
                self.last_used = self.fallback.name
                return out
            except LLMError as fallback_error:
                raise LLMError(
                    f"Primary LLM failed: {primary_error}; "
                    f"Fallback LLM failed: {fallback_error}"
                ) from fallback_error

def get_llm_client(settings: Optional[Settings] = None) -> Optional[LLMClient]:
    s = settings or get_settings()

    if s.llm_provider in ("", "none"):
        return None

    if s.llm_provider == "ollama":
        primary = OllamaClient(
            s.ollama_host,
            s.ollama_model,
            s.llm_temperature,
            s.llm_seed,
        )

        if s.llm_fallback_provider not in ("", "none"):
            if s.llm_fallback_provider == s.llm_provider:
                raise LLMError(f"LLM_FALLBACK_PROVIDER cannot be the same as LLM_PROVIDER ('{s.llm_provider}')")
            if s.llm_fallback_provider != "gemini":
                # Fail loudly rather than silently ignore a misconfigured/typo'd fallback (see AGENTS.md
                # Rule 6). Only 'gemini' is wired as an ollama fallback today; extend here if you add another.
                raise LLMError(f"LLM_FALLBACK_PROVIDER='{s.llm_fallback_provider}' is not supported for LLM_PROVIDER=ollama (only 'gemini' is wired)")
            if s.llm_local_only:
                raise LLMError(
                    "LLM_LOCAL_ONLY=true forbids Gemini fallback"
                )

            fallback = GeminiClient(
                s.gemini_api_key,
                s.gemini_model,
                s.llm_temperature,
                s.llm_seed,
            )

            return FallbackLLMClient(primary, fallback)

        return primary

    if s.llm_provider == "gemini":
        if s.llm_local_only:
            raise LLMError(
                "LLM_LOCAL_ONLY=true forbids the cloud provider 'gemini'"
            )

        return GeminiClient(
            s.gemini_api_key,
            s.gemini_model,
            s.llm_temperature,
            s.llm_seed,
        )

    if s.llm_provider == "anthropic":
        if s.llm_local_only:
            raise LLMError(
                "LLM_LOCAL_ONLY=true forbids the cloud provider 'anthropic'"
            )

        return AnthropicClient(
            s.anthropic_api_key,
            s.anthropic_model,
            s.llm_temperature,
            s.llm_seed,
        )

    raise LLMError(
        f"Unknown LLM_PROVIDER '{s.llm_provider}'"
    )
