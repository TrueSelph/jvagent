"""Baseline tests for ``CockpitEngine``: step state machine + termination paths.

Each test scripts a sequence of ``ModelActionResult`` returns from a
``ScriptedModelAction`` and asserts the engine's ``CockpitStepResult`` outputs,
message accumulation, and termination reason.
"""

from __future__ import annotations

import time

import pytest

from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.action.cockpit.engine import CockpitEngine

from .conftest import ScriptedModelAction, make_lm_result, make_tool_call

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_text_response_terminates_cleanly(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Model returns final text on first call → status=final_response, COMPLETED."""
    cockpit_ctx.model_action = ScriptedModelAction(
        [make_lm_result(response="Hello there.")]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    result = await engine.step()

    assert result.status == "final_response"
    assert result.termination_reason == TerminationReason.COMPLETED
    assert result.final_response == "Hello there."
    assert result.iterations == 1


async def test_tool_calls_then_text_completes_in_two_steps(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Step 1: tool_calls → status=tool_calls. Step 2: text → final_response."""
    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(tool_calls=[make_tool_call("echo", {"x": 1})]),
            make_lm_result(response="Done."),
        ]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()

    step1 = await engine.step()
    assert step1.status == "tool_calls"
    assert step1.iterations == 1

    step2 = await engine.step()
    assert step2.status == "final_response"
    assert step2.final_response == "Done."
    assert step2.iterations == 2

    # Messages: system + user + assistant(tool_calls) + tool_result + final assistant?
    # The engine appends assistant + tool result on tool_calls; final is returned via
    # CockpitStepResult, NOT appended to messages, so we check the tool-call accumulation.
    msgs = engine._messages
    roles = [m.get("role") for m in msgs]
    assert roles[0] == "system"
    assert roles[-2] == "assistant"  # tool_calls msg
    assert roles[-1] == "tool"


async def test_finalize_flag_terminates_after_dispatch(
    cockpit_ctx, patch_assemble_cockpit_tools, stub_tool_factory, stub_registry
):
    """response_publish(finalize=true) in batch with another tool → both dispatched, then completed."""

    # Inject a finalize tool that flips the cockpit_finalized flag mid-dispatch.
    visitor = cockpit_ctx.visitor

    def _set_finalized(**_kwargs):
        visitor._skill_state["cockpit_finalized"] = True
        return "published"

    stub_registry.register(
        stub_tool_factory(
            "response_publish", return_content="ok", side_effect=_set_finalized
        ),
        prefix="harness",
    )

    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(
                tool_calls=[
                    make_tool_call("echo", {"a": 1}),
                    make_tool_call(
                        "response_publish", {"content": "hi", "finalize": True}
                    ),
                ]
            )
        ]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    result = await engine.step()

    assert result.status == "final_response"
    assert result.termination_reason == TerminationReason.COMPLETED
    # final_response should be empty — content already published via response_publish
    assert result.final_response == ""
    # Both tools dispatched (echo + response_publish appear in tool_result messages)
    tool_msgs = [m for m in engine._messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


# ---------------------------------------------------------------------------
# Termination caps
# ---------------------------------------------------------------------------


async def test_iter_cap_terminates(cockpit_ctx, patch_assemble_cockpit_tools):
    """Looping tool_calls past max_iterations → ITER_CAP termination."""
    cockpit_ctx.config.max_iterations = 3
    # Script enough loops to exceed the cap.
    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(tool_calls=[make_tool_call("echo", {"i": i})])
            for i in range(10)
        ]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()

    # Run until terminal.
    last = None
    for _ in range(10):
        last = await engine.step()
        if last.status != "tool_calls":
            break

    assert last is not None
    assert last.status == "budget_exhausted"
    assert last.termination_reason == TerminationReason.ITER_CAP


async def test_time_cap_terminates(
    cockpit_ctx, patch_assemble_cockpit_tools, monkeypatch
):
    """Elapsed > max_duration_seconds → TIME_CAP termination."""
    cockpit_ctx.config.max_duration_seconds = 1.0
    cockpit_ctx.model_action = ScriptedModelAction(
        [make_lm_result(tool_calls=[make_tool_call("echo")])]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    # Warp the engine's start time backwards so the first step appears to have
    # already exceeded the duration budget.
    engine._start = time.monotonic() - 99.0

    result = await engine.step()
    assert result.status == "timeout"
    assert result.termination_reason == TerminationReason.TIME_CAP


async def test_all_errors_short_circuit(cockpit_ctx, patch_assemble_cockpit_tools):
    """Every tool call in the batch errors → ERROR termination."""
    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(
                tool_calls=[
                    make_tool_call("fail", {"a": 1}),
                    make_tool_call("fail", {"a": 2}),
                ]
            )
        ]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    result = await engine.step()

    assert result.status == "final_response"
    assert result.termination_reason == TerminationReason.ERROR
    assert "All tools returned errors" in (result.final_response or "")


# ---------------------------------------------------------------------------
# Stuck detection
# ---------------------------------------------------------------------------


async def test_stuck_detection_repeated_signature_fires(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Same tool + identical args repeated past the threshold → STUCK termination."""
    cockpit_ctx.config.stuck_min_iterations = 2
    cockpit_ctx.config.stuck_primary_tool_repeat = 3
    cockpit_ctx.config.stuck_detection_window = 3
    cockpit_ctx.config.max_iterations = 10

    same_call = make_tool_call("echo", {"q": "fixed"}, call_id="c1")
    cockpit_ctx.model_action = ScriptedModelAction(
        [make_lm_result(tool_calls=[same_call]) for _ in range(10)]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()

    last = None
    for _ in range(10):
        last = await engine.step()
        if last.status != "tool_calls":
            break

    assert last is not None
    assert last.status == "stuck"
    assert last.termination_reason == TerminationReason.STUCK


async def test_stuck_detection_does_not_fire_for_arg_refinement(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Same tool name with progressively different args is refinement, not stuck."""
    cockpit_ctx.config.stuck_min_iterations = 2
    cockpit_ctx.config.stuck_primary_tool_repeat = 3
    cockpit_ctx.config.stuck_detection_window = 3
    cockpit_ctx.config.max_iterations = 5

    cockpit_ctx.model_action = ScriptedModelAction(
        [
            make_lm_result(
                tool_calls=[
                    make_tool_call("echo", {"q": f"refine_{i}"}, call_id=f"c{i}")
                ]
            )
            for i in range(4)
        ]
        + [make_lm_result(response="ok")]
    )

    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()

    last = None
    for _ in range(6):
        last = await engine.step()
        if last.status not in ("tool_calls",):
            break

    assert last is not None
    # Should reach final_response (or hit ITER_CAP), but NOT STUCK.
    assert last.termination_reason != TerminationReason.STUCK
