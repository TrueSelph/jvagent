"""Tool errors must surface as thoughts, never as user-facing replies."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jvagent.action.cockpit.engine import CockpitEngine

from .conftest import ScriptedModelAction, make_lm_result, make_tool_call


@pytest.mark.asyncio
async def test_all_errors_short_circuit_emits_thought_not_reply(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Reply must be neutral; per-tool error trace must publish as a thought."""
    cockpit_ctx.config.stream_internal_progress = True
    cockpit_ctx.response_bus.publish = AsyncMock()

    # Engine sees a tool_calls step where every dispatched tool returns is_error.
    # ``patch_assemble_cockpit_tools`` substitutes a registry whose tools always
    # error in the test fixture, but to be explicit we configure the model to
    # request a tool that the registry will resolve as missing — same effect:
    # ToolExecutor records is_error=True for the call.
    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(
                tool_calls=[
                    make_tool_call("memory_set", {"key": "name", "value": "Eldon"})
                ]
            ),
        ]
    )
    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    step_result = await engine.step()

    # The user-facing reply must be neutral — no "Tool execution failed",
    # no tool name, no "all tools returned errors".
    reply = (step_result.final_response or "").lower()
    assert (
        "tool" not in reply
    ), f"reply leaked tool language: {step_result.final_response!r}"
    assert "memory_set" not in reply
    assert "error" not in reply
    assert "fail" not in reply
    # The neutral fallback should still ask the user to retry / rephrase.
    assert "rephrase" in reply or "try again" in reply

    # The per-tool detail must have been published as a thought.
    publish_calls = cockpit_ctx.response_bus.publish.await_args_list
    thought_calls = [c for c in publish_calls if c.kwargs.get("category") == "thought"]
    assert thought_calls, "expected at least one category=thought publish"
    tool_error_thoughts = [
        c for c in thought_calls if c.kwargs.get("thought_type") == "tool_error"
    ]
    assert tool_error_thoughts, "expected a thought_type=tool_error publish"
    assert "memory_set" in tool_error_thoughts[0].kwargs["content"]


@pytest.mark.asyncio
async def test_all_errors_short_circuit_skips_thought_when_streaming_off(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """When stream_internal_progress is off, no thought publish, but reply still neutral."""
    cockpit_ctx.config.stream_internal_progress = False
    cockpit_ctx.response_bus.publish = AsyncMock()
    cockpit_ctx.model_action = ScriptedModelAction(
        [make_lm_result(tool_calls=[make_tool_call("memory_set", {})])]
    )
    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    step_result = await engine.step()

    reply = (step_result.final_response or "").lower()
    assert "tool" not in reply and "error" not in reply

    publish_calls = cockpit_ctx.response_bus.publish.await_args_list
    tool_error_thoughts = [
        c for c in publish_calls if c.kwargs.get("thought_type") == "tool_error"
    ]
    assert not tool_error_thoughts, "thought must be suppressed when streaming off"
