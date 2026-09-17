"""Tool definitions on the ``model_call`` event: first unique surface, then names.

The full JSON Schemas are what a debug UI needs to replay a request exactly,
but they are byte-identical on every tick of an agentic turn. The first
``model_call`` of a unique ``tool_names`` set stores them; later ticks of the
same surface keep names only. ``telemetry_tool_definitions`` still stores
schemas on every tick. Tool names ride along always.
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

OTHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _result(**overrides):
    data = dict(
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
        temperature=None,
        max_tokens=None,
        tool_choice=None,
        parallel_tool_calls=None,
        _usage_estimated=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


async def _emit(action, interaction=None, result=None):
    if interaction is None:
        interaction = MagicMock()
        interaction.observability_metrics = []
        interaction.save = AsyncMock()
    await action._emit_observability(
        interaction, {"total_tokens": 2}, 0.1, result=result or _result()
    )
    return interaction


async def test_first_tick_stores_tools_and_later_same_surface_keeps_names_only():
    action = _Stub()
    interaction = await _emit(action)
    first = interaction.observability_metrics[0]["data"]
    assert first["tools"] == TOOLS
    assert first["tool_names"] == ["reply", "update_plan"]

    await _emit(action, interaction=interaction)
    second = interaction.observability_metrics[1]["data"]
    assert "tools" not in second
    assert second["tool_names"] == ["reply", "update_plan"]


async def test_a_new_tool_surface_stores_tools_again():
    action = _Stub()
    interaction = await _emit(action)
    await _emit(action, interaction=interaction, result=_result(tools=OTHER_TOOLS))
    third_surface = interaction.observability_metrics[1]["data"]
    assert third_surface["tools"] == OTHER_TOOLS
    assert third_surface["tool_names"] == ["search"]


async def test_definitions_are_stored_on_every_tick_when_the_knob_is_on():
    action = _Stub()
    action.telemetry_tool_definitions = True
    interaction = await _emit(action)
    await _emit(action, interaction=interaction)
    assert interaction.observability_metrics[0]["data"]["tools"] == TOOLS
    assert interaction.observability_metrics[1]["data"]["tools"] == TOOLS


async def test_generation_params_are_recorded_when_present():
    data = (
        await _emit(
            _Stub(),
            result=_result(
                temperature=0.2,
                max_tokens=1024,
                tool_choice="auto",
                parallel_tool_calls=False,
            ),
        )
    ).observability_metrics[0]["data"]
    assert data["temperature"] == 0.2
    assert data["max_tokens"] == 1024
    assert data["tool_choice"] == "auto"
    assert data["parallel_tool_calls"] is False


async def test_generation_params_are_omitted_when_unset():
    data = (await _emit(_Stub())).observability_metrics[0]["data"]
    assert "temperature" not in data
    assert "max_tokens" not in data
    assert "tool_choice" not in data
    assert "parallel_tool_calls" not in data


async def test_request_model_is_recorded_beside_the_resolved_model():
    """``model`` is what the provider answered with; ``request_model`` is what
    the agent asked for, and only the latter can be replayed."""
    data = (await _emit(_Stub())).observability_metrics[0]["data"]
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
