"""Transport delegation (ADR-0047): first-party actions can route their calls
through the LiteLLM adapter with their own model, credentials and endpoint."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.action.model.language.anthropic.anthropic import (
    AnthropicLanguageModelAction,
)
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.language.groq.groq import GroqLanguageModelAction
from jvagent.action.model.language.ollama.ollama import OllamaLanguageModelAction
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction
from jvagent.action.model.language.openrouter.openrouter import (
    OpenRouterLanguageModelAction,
)

pytest.importorskip("litellm")


def test_transport_defaults_to_httpx_and_env_overrides(monkeypatch):
    action = OpenAILanguageModelAction()
    monkeypatch.delenv("JVAGENT_MODEL_TRANSPORT", raising=False)
    assert action._effective_transport() == "httpx"
    action.transport = "litellm"
    assert action._effective_transport() == "litellm"
    action.transport = "httpx"
    monkeypatch.setenv("JVAGENT_MODEL_TRANSPORT", "litellm")
    assert action._effective_transport() == "litellm"
    monkeypatch.setenv("JVAGENT_MODEL_TRANSPORT", "nonsense")
    assert action._effective_transport() == "httpx"


def test_model_ids_and_credentials_map_per_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ol")

    openai = OpenAILanguageModelAction()
    assert openai.litellm_model_id() == "openai/gpt-4o-mini"
    assert (
        openai.litellm_model_id("openai/gpt-4o") == "openai/gpt-4o"
    )  # already prefixed
    assert openai.litellm_call_config() == {
        "api_key": "sk-openai"
    }  # default endpoint → no api_base
    openai.api_endpoint = "https://proxy.example/v1/"
    assert openai.litellm_call_config()["api_base"] == "https://proxy.example/v1"

    anthropic = AnthropicLanguageModelAction()
    assert (
        anthropic.litellm_model_id("claude-sonnet-4-5") == "anthropic/claude-sonnet-4-5"
    )
    assert anthropic.litellm_call_config() == {"api_key": "sk-anthropic"}

    assert GroqLanguageModelAction().litellm_model_id().startswith("groq/")
    assert GroqLanguageModelAction().litellm_call_config() == {"api_key": "gsk"}
    assert OpenRouterLanguageModelAction().litellm_call_config() == {
        "api_key": "or-key"
    }

    ollama = OllamaLanguageModelAction()
    ollama.api_endpoint = "http://box:11434/api"
    assert ollama.litellm_model_id("llama3.1:8b") == "ollama/llama3.1:8b"
    assert ollama.litellm_call_config() == {
        "api_base": "http://box:11434",
        "api_key": "ol",
    }


class _Delegate:
    """Stand-in for the LiteLLM adapter: records calls, returns a litellm-labelled result."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.api_key = ""
        self.api_base = ""
        self.provider = "litellm"

    async def _query(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, **kwargs})
        return ModelActionResult(
            response="via litellm",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model=kwargs["model"],
            provider="litellm",
            finish_reason="stop",
        )

    async def _query_stream(self, messages, tools=None, **kwargs):
        self.calls.append({"stream": True, **kwargs})

        async def gen():
            yield "via "
            yield "litellm"

        return ModelActionResult(
            stream=gen(), model=kwargs["model"], provider="litellm"
        )


@pytest.mark.asyncio
async def test_litellm_transport_delegates_and_relabels_the_result(monkeypatch):
    monkeypatch.delenv("JVAGENT_MODEL_TRANSPORT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    action = OpenAILanguageModelAction()
    action.transport = "litellm"
    action.max_retries = 0
    delegate = _Delegate()
    monkeypatch.setattr(action, "_litellm_delegate", lambda: delegate)

    result = await action.query_messages(
        messages=[{"role": "user", "content": "hi"}], stream=False, model="gpt-4o"
    )
    assert await result.get_response() == "via litellm"
    # The caller sees THIS action's identity, not the delegate's.
    assert result.provider == "openai" and result.model == "gpt-4o"
    assert delegate.calls[0]["model"] == "openai/gpt-4o"

    streamed = await action.query_messages(
        messages=[{"role": "user", "content": "hi"}], stream=True
    )
    assert await streamed.get_response() == "via litellm"
    assert streamed.provider == "openai" and streamed.model == "gpt-4o-mini"
    assert delegate.calls[1]["model"] == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_httpx_transport_does_not_touch_the_delegate(monkeypatch):
    monkeypatch.delenv("JVAGENT_MODEL_TRANSPORT", raising=False)
    action = OpenAILanguageModelAction()
    action.max_retries = 0
    delegate = _Delegate()
    monkeypatch.setattr(action, "_litellm_delegate", lambda: delegate)

    async def own_query(messages, tools=None, **kwargs):
        return ModelActionResult(
            response="own wire", model="gpt-4o-mini", provider="openai"
        )

    monkeypatch.setattr(action, "_query", own_query)
    result = await action.query_messages(messages=[{"role": "user", "content": "hi"}])
    assert await result.get_response() == "own wire"
    assert delegate.calls == []


def test_delegate_is_reused_and_reconfigured_each_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    action = AnthropicLanguageModelAction()
    first = action._litellm_delegate()
    assert first.api_key == "k1" and first.provider == "anthropic"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")
    second = action._litellm_delegate()
    assert second is first and second.api_key == "k2"


@pytest.mark.asyncio
async def test_model_call_telemetry_names_the_transport(monkeypatch):
    """A canary verifies the switch from telemetry: the delegated result is
    relabelled as the action's own provider (by design), so ``transport`` is the
    one field that says which wire carried the call."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from jvagent.action.model.language.litellm.litellm_lm import (
        LiteLLMLanguageModelAction,
    )

    def _interaction():
        i = MagicMock()
        i.observability_metrics = []
        i.save = AsyncMock()
        return i

    def _result(provider):
        return SimpleNamespace(
            system="s",
            prompt="p",
            history=[],
            response="r",
            provider=provider,
            model="gpt-4.1",
            metrics={"total_tokens": 2},
            is_streaming=False,
            calling_action_name="Orchestrator",
            finish_reason="stop",
            tool_calls=None,
            _usage_estimated=False,
        )

    monkeypatch.delenv("JVAGENT_MODEL_TRANSPORT", raising=False)
    action = OpenAILanguageModelAction()
    seen = {}
    for transport in ("httpx", "litellm"):
        action.transport = transport
        interaction = _interaction()
        await action._emit_observability(
            interaction, {"total_tokens": 2}, 0.1, result=_result("openai")
        )
        data = interaction.observability_metrics[0]["data"]
        seen[transport] = (data["provider"], data["transport"])
    assert seen == {"httpx": ("openai", "httpx"), "litellm": ("openai", "litellm")}

    # The env override is what a canary flips; it wins over the attribute.
    action.transport = "httpx"
    monkeypatch.setenv("JVAGENT_MODEL_TRANSPORT", "litellm")
    interaction = _interaction()
    await action._emit_observability(
        interaction, {"total_tokens": 2}, 0.1, result=_result("openai")
    )
    assert interaction.observability_metrics[0]["data"]["transport"] == "litellm"

    # The adapter itself always reports litellm.
    adapter = LiteLLMLanguageModelAction()
    interaction = _interaction()
    await adapter._emit_observability(
        interaction, {"total_tokens": 2}, 0.1, result=_result("litellm")
    )
    assert interaction.observability_metrics[0]["data"]["transport"] == "litellm"
