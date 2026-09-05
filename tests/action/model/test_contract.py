"""The normalised model contract (``jvagent.action.model.contract``)."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.model.contract import (
    FinishReason,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ToolCall,
    Usage,
    normalize_finish_reason,
)
from jvagent.action.model.language.base import ModelActionResult

# --- finish reasons -----------------------------------------------------------


def test_finish_reasons_normalise_across_providers():
    assert normalize_finish_reason("stop") == FinishReason.STOP
    assert normalize_finish_reason("end_turn") == FinishReason.STOP
    assert normalize_finish_reason("stop_sequence") == FinishReason.STOP
    assert normalize_finish_reason("length") == FinishReason.LENGTH
    assert normalize_finish_reason("max_tokens") == FinishReason.LENGTH
    assert normalize_finish_reason("MAX_TOKENS") == FinishReason.LENGTH
    assert normalize_finish_reason("tool_calls") == FinishReason.TOOL_CALLS
    assert normalize_finish_reason("tool_use") == FinishReason.TOOL_CALLS
    assert normalize_finish_reason("content_filter") == FinishReason.CONTENT_FILTER
    assert normalize_finish_reason("refusal") == FinishReason.CONTENT_FILTER
    assert normalize_finish_reason("weird") == FinishReason.UNKNOWN


def test_tool_calls_outrank_a_stop_label():
    """Ollama labels a tool-calling turn ``stop``; what the model did wins."""
    assert normalize_finish_reason("stop", has_tool_calls=True) == FinishReason.TOOL_CALLS
    assert normalize_finish_reason(None, has_tool_calls=True) == FinishReason.TOOL_CALLS
    assert normalize_finish_reason(None) == FinishReason.STOP
    # A truncated tool call stays truncated — the arguments may be incomplete.
    assert normalize_finish_reason("length", has_tool_calls=True) == FinishReason.LENGTH


# --- tool calls ---------------------------------------------------------------


def test_tool_call_parses_string_and_dict_arguments():
    a = ToolCall.from_openai(
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}}
    )
    assert a and a.arguments == {"x": 1} and a.raw_arguments == '{"x": 1}'
    b = ToolCall.from_openai({"id": "c2", "function": {"name": "f", "arguments": {"y": 2}}})
    assert b and b.arguments == {"y": 2}
    bad = ToolCall.from_openai({"id": "c3", "function": {"name": "f", "arguments": "{oops"}})
    assert bad and bad.arguments == {} and bad.raw_arguments == "{oops"
    assert ToolCall.from_openai({"id": "c4", "function": {}}) is None
    assert ToolCall.from_openai("nope") is None
    assert a.to_openai() == {
        "id": "c1",
        "type": "function",
        "function": {"name": "f", "arguments": '{"x": 1}'},
    }


# --- usage --------------------------------------------------------------------


def test_usage_reads_every_cache_spelling():
    openai_flat = Usage.from_metrics({"prompt_tokens": 100, "completion_tokens": 5, "cached_tokens": 60})
    assert (openai_flat.cached_read_tokens, openai_flat.total_tokens) == (60, 105)
    openai_nested = Usage.from_metrics(
        {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40}}
    )
    assert openai_nested.cached_read_tokens == 40
    anthropic = Usage.from_metrics(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 3,
            "total_tokens": 1003,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        }
    )
    assert (anthropic.cached_read_tokens, anthropic.cached_write_tokens) == (800, 100)
    reasoning = Usage.from_metrics(
        {"prompt_tokens": 1, "completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 30}}
    )
    assert reasoning.thinking_tokens == 30
    assert Usage.from_metrics(None).total_tokens == 0
    assert Usage.from_metrics({"prompt_tokens": "junk"}).prompt_tokens == 0


# --- request → kwargs ---------------------------------------------------------


def test_request_only_forwards_set_fields():
    req = ModelRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=10, extra={"thinking": {"type": "enabled"}})
    kwargs = req.to_query_kwargs()
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["stream"] is False and kwargs["tools"] is None
    assert kwargs["max_tokens"] == 10 and kwargs["thinking"] == {"type": "enabled"}
    assert "temperature" not in kwargs and "tool_choice" not in kwargs


# --- response from legacy result ---------------------------------------------


def test_response_from_legacy_result_normalises_everything():
    result = ModelActionResult(
        response="",
        usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "cache_read_input_tokens": 6},
        model="claude",
        provider="anthropic",
        finish_reason="tool_use",
        tool_calls=[{"id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}],
        thinking_content="hmm",
        duration=0.25,
    )
    resp = result.to_response()
    assert resp.finish_reason == FinishReason.TOOL_CALLS and resp.raw_finish_reason == "tool_use"
    assert resp.tool_calls[0].arguments == {"a": 1}
    assert resp.usage.cached_read_tokens == 6 and resp.usage.total_tokens == 14
    assert resp.thinking == "hmm" and resp.provider == "anthropic" and resp.latency_ms == 250
    assert resp.has_tool_calls and not resp.truncated
    assert resp.tool_calls_openai()[0]["function"]["name"] == "f"


def test_response_from_duck_typed_double_and_passthrough():
    """A test double exposing only ``response`` (the wire probe does) is a
    text-only stop; a ModelResponse passes through untouched."""
    resp = ModelResponse.from_result(SimpleNamespace(response='{"action":"final"}'))
    assert resp.text == '{"action":"final"}' and resp.finish_reason == FinishReason.STOP
    assert resp.tool_calls == [] and resp.usage.total_tokens == 0
    assert ModelResponse.from_result(None).text == ""
    same = ModelResponse(text="x")
    assert ModelResponse.from_result(same) is same
    truncated = ModelResponse.from_result(SimpleNamespace(response="par", finish_reason="max_tokens"))
    assert truncated.truncated


def test_capabilities_default_to_unknown_not_guessed():
    caps = ModelCapabilities()
    assert caps.supports_tools is None and caps.context_window is None
    assert caps.source == "unknown"
