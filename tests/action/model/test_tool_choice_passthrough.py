"""``tool_choice`` / ``parallel_tool_calls`` reach the provider payloads (ADR-0044).

The orchestrator's native protocol asks for exactly one tool call per tick;
OpenAI-family actions pass the controls through verbatim (only alongside
``tools``), Anthropic maps them to its ``tool_choice`` block.
"""

from __future__ import annotations

from jvagent.action.model.language.anthropic.anthropic import (
    AnthropicLanguageModelAction,
)
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "Reply.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
_MESSAGES = [{"role": "user", "content": "hi"}]


def test_openai_payload_passes_tool_controls_only_with_tools():
    action = OpenAILanguageModelAction()
    payload = action._build_openai_payload(
        _MESSAGES, _TOOLS, stream=False, tool_choice="auto", parallel_tool_calls=False
    )
    assert payload["tools"] == _TOOLS
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False

    bare = action._build_openai_payload(
        _MESSAGES, None, stream=False, tool_choice="auto", parallel_tool_calls=False
    )
    assert "tool_choice" not in bare and "parallel_tool_calls" not in bare


def test_anthropic_payload_maps_tool_controls():
    action = AnthropicLanguageModelAction()
    payload = action._build_payload(
        _MESSAGES,
        tools=_TOOLS,
        stream=False,
        tool_choice="auto",
        parallel_tool_calls=False,
    )
    assert payload["tools"][0]["name"] == "reply"
    assert payload["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}

    bare = action._build_payload(
        _MESSAGES, tools=None, stream=False, tool_choice="auto"
    )
    assert "tool_choice" not in bare


def test_anthropic_tool_choice_mapping_shapes():
    m = AnthropicLanguageModelAction._map_tool_choice
    assert m("required", None) == {"type": "any"}
    assert m({"type": "function", "function": {"name": "x"}}, None) == {
        "type": "tool",
        "name": "x",
    }
    assert m(None, None) is None
    assert m(None, False) == {"type": "auto", "disable_parallel_tool_use": True}
    assert m("none", None) is None
