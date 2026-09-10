"""Tool definitions on the ``model_call`` event are opt-in; names are not.

The full JSON Schemas are what a debug UI needs to replay a request exactly,
but they are byte-identical on every tick of an agentic turn, so writing them
into each event persists the same payload once per tick per interaction.
``telemetry_tool_definitions`` gates them; the tool names ride along always
because they cost a handful of bytes and answer most "what was on the surface?"
questions on their own.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.model.base import BaseModelAction, _tool_definition_names

pytestmark = pytest.mark.asyncio


class _Stub(BaseModelAction):
    pass


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "Send the reply.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Record a plan.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _result():
    return SimpleNamespace(
        system="s",
        prompt="p",
        history=[],
        response="r",
        provider="openai",
        model="gpt-4.1-2025-04-14",
        request_model="openai/gpt-4.1",
        metrics={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        is_streaming=False,
        calling_action_name="orchestrator",
        finish_reason="stop",
        tool_calls=None,
        tools=TOOLS,
        _usage_estimated=False,
    )


async def _emit(action):
    interaction = MagicMock()
    interaction.observability_metrics = []
    interaction.save = AsyncMock()
    await action._emit_observability(
        interaction, {"total_tokens": 2}, 0.1, result=_result()
    )
    return interaction.observability_metrics[0]["data"]


async def test_definitions_are_omitted_by_default_but_names_are_kept():
    data = await _emit(_Stub())
    assert "tools" not in data
    assert data["tool_names"] == ["reply", "update_plan"]


async def test_definitions_are_stored_when_the_knob_is_on():
    action = _Stub()
    action.telemetry_tool_definitions = True
    data = await _emit(action)
    assert data["tools"] == TOOLS
    assert data["tool_names"] == ["reply", "update_plan"]


async def test_request_model_is_recorded_beside_the_resolved_model():
    """``model`` is what the provider answered with; ``request_model`` is what
    the agent asked for, and only the latter can be replayed."""
    data = await _emit(_Stub())
    assert data["model"] == "gpt-4.1-2025-04-14"
    assert data["request_model"] == "openai/gpt-4.1"


async def test_tool_names_reads_both_wire_shapes_and_skips_junk():
    assert _tool_definition_names(
        [
            {"type": "function", "function": {"name": "a"}},
            {"name": "b"},
            {"function": {}},
            {},
            "nonsense",
        ]
    ) == ["a", "b"]
    assert _tool_definition_names(None) == []
    assert _tool_definition_names("nope") == []
