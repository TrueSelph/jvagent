"""LiteLLMLanguageModelAction request/response mapping (ADR-0045)."""

from __future__ import annotations

import json

import pytest

from jvagent.action.model.language.litellm import LiteLLMLanguageModelAction

litellm = pytest.importorskip("litellm")


def _action(**attrs) -> LiteLLMLanguageModelAction:
    action = LiteLLMLanguageModelAction()
    for key, value in attrs.items():
        setattr(action, key, value)
    return action


def test_build_kwargs_carries_tool_controls_only_with_tools():
    action = _action(
        api_key="k", api_base="http://proxy", extra_params={"metadata": {"x": 1}}
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "f",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    kw = action._build_kwargs(
        [{"role": "user", "content": "hi"}],
        tools,
        stream=True,
        tool_choice="auto",
        parallel_tool_calls=False,
        max_tokens=50,
        reasoning={"effort": "low"},
    )
    assert kw["model"] == "openai/gpt-4o-mini" and kw["stream"] is True
    assert kw["stream_options"] == {"include_usage": True}
    assert kw["tools"] == tools and kw["tool_choice"] == "auto"
    assert kw["parallel_tool_calls"] is False and kw["max_tokens"] == 50
    assert kw["reasoning_effort"] == "low"
    assert kw["api_key"] == "k" and kw["api_base"] == "http://proxy"
    assert kw["drop_params"] is True and kw["num_retries"] == 0
    assert kw["metadata"] == {"x": 1}

    bare = action._build_kwargs(
        [{"role": "user", "content": "hi"}], None, stream=False, tool_choice="auto"
    )
    assert (
        "tools" not in bare
        and "tool_choice" not in bare
        and "stream_options" not in bare
    )


def test_result_mapping_from_a_real_litellm_response():
    body = {
        "id": "x",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "thinking...",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": '{"a": 1}'},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }
    result = _action()._result_from_response(
        litellm.ModelResponse(**body), "openai/gpt-4o-mini"
    )
    assert result.response == "" and result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "f", "arguments": '{"a": 1}'},
        }
    ]
    assert (
        result.metrics["cached_tokens"] == 40 and result.metrics["thinking_tokens"] == 3
    )
    assert result.thinking_content == "thinking..." and result.provider == "litellm"
    response = result.to_response()
    assert (
        response.usage.cached_read_tokens == 40 and response.usage.thinking_tokens == 3
    )
    assert response.tool_calls[0].arguments == {"a": 1}


def test_usage_and_thinking_helpers_cover_anthropic_shapes():
    usage = LiteLLMLanguageModelAction._usage_from(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 3,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 20,
        }
    )
    assert usage["cached_tokens"] == 800 and usage["cache_creation_input_tokens"] == 20
    assert usage["total_tokens"] == 1003
    thinking = LiteLLMLanguageModelAction._thinking_from(
        {
            "provider_specific_fields": {
                "thinking_blocks": [{"type": "thinking", "thinking": "6*7"}]
            }
        }
    )
    assert (
        thinking is None
    )  # dict access only reads reasoning keys; objects carry the field

    class _Msg:
        reasoning_content = None
        provider_specific_fields = {
            "thinking_blocks": [{"type": "thinking", "thinking": "6*7"}]
        }

    assert LiteLLMLanguageModelAction._thinking_from(_Msg()) == "6*7"
    assert (
        LiteLLMLanguageModelAction._tool_calls_from(
            {"tool_calls": [{"function": {"name": "", "arguments": {}}}]}
        )
        == []
    )


@pytest.mark.asyncio
async def test_missing_litellm_is_a_clear_runtime_error(monkeypatch):
    action = _action()

    def _boom():
        raise RuntimeError("LiteLLMLanguageModelAction needs the 'litellm' package")

    monkeypatch.setattr(LiteLLMLanguageModelAction, "_litellm", staticmethod(_boom))
    with pytest.raises(RuntimeError, match="needs the 'litellm' package"):
        await action._query([{"role": "user", "content": "hi"}])


def test_capabilities_and_pricing_come_from_upstream_metadata():
    action = _action(model="anthropic/claude-sonnet-4-5")
    caps = action.capabilities()
    assert caps.supports_tools is True and caps.context_window == 200_000
    assert action.pricing().source == "litellm"
    assert json.dumps(
        action.capabilities("openai/gpt-4o-mini").__dict__
    )  # serialisable
