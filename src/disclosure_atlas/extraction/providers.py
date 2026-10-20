"""LLM providers behind one small interface: JSON in a given schema, from a system prompt and a document.

Configuration comes from the environment, and none is required:

- ``ATLAS_LLM_PROVIDER``: ``anthropic``, ``ollama`` or ``none``. When unset, ``anthropic`` is used if
  ``ANTHROPIC_API_KEY`` is set, else ``ollama`` if ``OLLAMA_HOST`` is set, else no provider.
- ``ATLAS_LLM_MODEL``: overrides the model (defaults: ``claude-opus-5-5`` and ``qwen3:14b``).
- ``OLLAMA_HOST``: Ollama base URL (default ``http://localhost:11434``).

Sending a document to a hosted model sends the declarant's name with it. That is why the fallback is opt-in
and why a local model (Ollama) is a first-class option; see docs/sources/fr_amf_pdmr.md.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol, runtime_checkable

import httpx

PROVIDER_ENV = "ATLAS_LLM_PROVIDER"
MODEL_ENV = "ATLAS_LLM_MODEL"
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer, tolerating Markdown fences or text around it."""
    text = text.strip()
    candidates = [text]
    if fenced := re.search(r"```(?:json)?\s*(.*?)```", text, re.S):
        candidates.append(fenced.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ProviderError("the answer contains no JSON object")


class ProviderError(RuntimeError):
    """The provider could not return a usable answer (refusal, truncation, transport error)."""


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model(self) -> str: ...

    def complete_json(self, system: str, document: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object conforming to ``schema`` (validation is the caller's job)."""


class AnthropicProvider:
    """Claude API with JSON-schema structured outputs (``output_config.format``)."""

    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL, client: Any | None = None, effort: str = "medium") -> None:
        if client is None:
            import anthropic  # optional dependency: pip install -e '.[llm]'

            client = anthropic.Anthropic()
        self._client = client
        self._model = model
        self.effort = effort

    @property
    def model(self) -> str:
        return self._model

    def complete_json(self, system: str, document: str, schema: dict[str, Any]) -> dict[str, Any]:
        # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
        with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": document}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason in {"refusal", "max_tokens"}:
            raise ProviderError(f"{self._model} stopped with {response.stop_reason}")
        return parse_json_object(next((block.text for block in response.content if block.type == "text"), ""))


class OllamaProvider:
    """A local model served by Ollama, constrained by the JSON schema through ``format``."""

    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, host: str | None = None, timeout: float = 600.0) -> None:
        self._model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        if "://" not in self.host:
            self.host = f"http://{self.host}"
        self.timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    def complete_json(self, system: str, document: str, schema: dict[str, Any]) -> dict[str, Any]:
        # Not every local model honours ``format`` strictly, so the schema is also spelled out in the prompt.
        # Thinking is off: reasoning models otherwise spend the output budget before writing the answer.
        system = f"{system}\nAnswer with one JSON object that validates against this JSON schema:\n{json.dumps(schema)}"
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": 32768},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": document}],
        }
        try:
            response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"ollama {self._model}: {exc}") from exc
        return parse_json_object(content)


def provider_from_env(env: dict[str, str] | None = None) -> LLMProvider | None:
    """The configured provider, or None when no LLM is configured (the default)."""
    env = dict(os.environ) if env is None else env
    choice = (env.get(PROVIDER_ENV) or "").strip().casefold()
    if not choice:
        choice = "anthropic" if env.get("ANTHROPIC_API_KEY") else "ollama" if env.get("OLLAMA_HOST") else "none"
    model = env.get(MODEL_ENV) or None
    if choice == "none":
        return None
    if choice == "anthropic":
        return AnthropicProvider(model=model or DEFAULT_CLAUDE_MODEL)
    if choice == "ollama":
        return OllamaProvider(model=model or DEFAULT_OLLAMA_MODEL, host=env.get("OLLAMA_HOST"))
    raise ValueError(f"unknown {PROVIDER_ENV}={choice!r}; expected anthropic, ollama or none")
