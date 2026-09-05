"""Fixtures for the provider conformance suite.

``adapter`` builds one provider action per parametrised case with its HTTP
client swapped for a replay (or, when recording, recording) transport. Nothing
here touches the network unless ``JVAGENT_CONFORMANCE_RECORD=1`` **and** the
provider's key is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

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
}


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
    provider: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> Optional[AdapterCase]:
    """Build the adapter for one (provider, scenario), or None to skip."""
    action = _build_action(provider, monkeypatch)
    recording = recording_enabled()
    if recording:
        key_env = PROVIDERS[provider][2]
        import os

        if key_env and not os.environ.get(key_env):
            return None  # cannot record without a key
        transport: Any = RecordingTransport()
        fixture = {"source": "recorded", "provider": provider, "scenario": scenario}
    else:
        fixture = load_recorded(provider, scenario) or authored_fixture(provider, scenario)
        transport = ReplayTransport(fixture["responses"])
    action._http_client = httpx.AsyncClient(transport=transport)
    # Untracked client → adopted for the current loop (see _initialize_http_client).
    action._http_client_loop_id = None
    return AdapterCase(
        provider=provider,
        action=action,
        transport=transport,
        fixture=fixture,
        recording=recording,
    )
