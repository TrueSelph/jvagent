"""Fixtures for the provider conformance suite.

``make_case`` builds one provider action per parametrised case with its HTTP
client swapped for a replay (or, when recording, recording) transport. Nothing
here touches the network unless ``JVAGENT_CONFORMANCE_RECORD=1`` **and** the
provider's key is present.

Two adapter families:

- first-party httpx adapters (openai, anthropic, ollama, groq, openrouter),
  exercised under both transports (ADR-0047): their own wire client, and
  delegation to the LiteLLM adapter;
- the LiteLLM universal adapter itself (``litellm``), replayed through its
  ``_acompletion`` seam as real ``litellm`` response objects built from the
  OpenAI wire bodies (LiteLLM's response shape is OpenAI's whatever the
  underlying provider).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
import pytest

from tests.action.model.conformance._transport import (
    RecordingTransport,
    ReplayTransport,
    load_recorded,
    recording_enabled,
)
from tests.action.model.conformance.authored import authored_fixture

# provider → (action module path, class name, key env var or None)
PROVIDERS: Dict[str, tuple] = {
    "openai": (
        "jvagent.action.model.language.openai.openai",
        "OpenAILanguageModelAction",
        "OPENAI_API_KEY",
    ),
    "anthropic": (
        "jvagent.action.model.language.anthropic.anthropic",
        "AnthropicLanguageModelAction",
        "ANTHROPIC_API_KEY",
    ),
    "ollama": (
        "jvagent.action.model.language.ollama.ollama",
        "OllamaLanguageModelAction",
        None,
    ),
    "groq": (
        "jvagent.action.model.language.groq.groq",
        "GroqLanguageModelAction",
        "GROQ_API_KEY",
    ),
    "openrouter": (
        "jvagent.action.model.language.openrouter.openrouter",
        "OpenRouterLanguageModelAction",
        "OPENROUTER_API_KEY",
    ),
    # Universal adapter (ADR-0045). Replays through ``_acompletion`` rather than
    # httpx; recording is not supported for it (record the first-party providers).
    "litellm": (
        "jvagent.action.model.language.litellm.litellm_lm",
        "LiteLLMLanguageModelAction",
        "OPENAI_API_KEY",
    ),
}

# Transports a first-party adapter is exercised under (ADR-0047): its own httpx
# wire client, and delegation to the LiteLLM adapter. The ``litellm`` provider
# entry is the adapter itself and has no transport axis.
TRANSPORTS = ("httpx", "litellm")


class _LiteLLMTransport:
    """Fixture-backed stand-in for ``litellm.acompletion``.

    Mirrors the httpx transports' surface (``requests`` / ``request_json``) so
    the same assertions apply. HTTP errors become the litellm exception the
    real client would raise (they carry ``status_code``, which the harness
    retry policy reads); a malformed body fails to construct, as it would.
    """

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.requests: List[Dict[str, Any]] = []

    def request_json(self, index: int = -1) -> Dict[str, Any]:
        return dict(self.requests[index])

    async def __call__(self, **kwargs: Any) -> Any:
        import json

        import litellm

        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("conformance: more requests than fixture responses")
        spec = self._responses.pop(0)
        status = int(spec.get("status", 200))
        body = spec.get("body", "")
        if status >= 400:
            model = str(kwargs.get("model") or "")
            if status == 429:
                raise litellm.RateLimitError(
                    message="rate limited", llm_provider="openai", model=model
                )
            if status >= 500:
                raise litellm.InternalServerError(
                    message="server error", llm_provider="openai", model=model
                )
            raise litellm.APIError(
                status_code=status, message="error", llm_provider="openai", model=model
            )
        if kwargs.get("stream"):
            chunks = [
                json.loads(line[6:])
                for line in str(body).splitlines()
                if line.startswith("data: ") and line[6:].strip() != "[DONE]"
            ]

            async def _gen():
                for chunk in chunks:
                    yield litellm.ModelResponseStream(**chunk)

            return _gen()
        return litellm.ModelResponse(**json.loads(body))


@dataclass
class AdapterCase:
    provider: str
    action: Any
    transport: Any
    fixture: Dict[str, Any]
    recording: bool

    def request_json(self, index: int = -1) -> Dict[str, Any]:
        return self.transport.request_json(index)

    @property
    def request_count(self) -> int:
        return len(self.transport.requests)


def _build_action(provider: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib

    module_path, class_name, key_env = PROVIDERS[provider]
    cls = getattr(importlib.import_module(module_path), class_name)
    action = cls()
    if key_env and not recording_enabled():
        monkeypatch.setenv(key_env, "conformance-test-key")
    # Deterministic retry timing; scenarios override max_retries as needed.
    action.retry_jitter = False
    action.retry_initial_delay = 0.0
    action.retry_max_delay = 0.0
    action.max_retries = 1
    return action


def make_case(
    provider: str,
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    transport: str = "httpx",
) -> Optional[AdapterCase]:
    """Build the adapter for one (provider, scenario[, transport]), or None to skip."""
    action = _build_action(provider, monkeypatch)
    recording = recording_enabled()
    monkeypatch.delenv("JVAGENT_MODEL_TRANSPORT", raising=False)

    if provider == "litellm" or transport == "litellm":
        if recording:
            return None  # record on the httpx wire; LiteLLM replays it
        fixture = load_recorded("litellm", scenario) or authored_fixture(
            "litellm", scenario
        )
        fake: Any = _LiteLLMTransport(fixture["responses"])
        if provider == "litellm":
            action._acompletion = fake  # type: ignore[method-assign]
        else:
            action.transport = "litellm"
            action._litellm_delegate()._acompletion = fake  # type: ignore[method-assign]
        return AdapterCase(
            provider=provider,
            action=action,
            transport=fake,
            fixture=fixture,
            recording=False,
        )

    if recording:
        key_env = PROVIDERS[provider][2]
        import os

        if key_env and not os.environ.get(key_env):
            return None  # cannot record without a key
        transport_obj: Any = RecordingTransport()
        fixture = {"source": "recorded", "provider": provider, "scenario": scenario}
    else:
        fixture = load_recorded(provider, scenario) or authored_fixture(
            provider, scenario
        )
        transport_obj = ReplayTransport(fixture["responses"])
    action._http_client = httpx.AsyncClient(transport=transport_obj)
    # Untracked client → adopted for the current loop (see _initialize_http_client).
    action._http_client_loop_id = None
    return AdapterCase(
        provider=provider,
        action=action,
        transport=transport_obj,
        fixture=fixture,
        recording=recording,
    )
