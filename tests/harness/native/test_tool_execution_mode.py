"""Tool scheduling belongs to the public SDK and stays local to each run."""

import asyncio
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import run_native_agent


def probe_agent() -> tuple[Any, dict[str, int]]:
    state = {"active": 0, "peak": 0, "model_calls": 0, "tools": 0}

    async def probe(reference: str) -> str:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.02)
            state["tools"] += 1
            return reference
        finally:
            state["active"] -= 1

    def respond(messages: Any, info: Any) -> ModelResponse:
        state["model_calls"] += 1
        if state["model_calls"] == 1:
            return ModelResponse(
                parts=[ToolCallPart("probe", {"reference": str(n)}) for n in range(3)]
            )
        return ModelResponse(parts=[TextPart("done")])

    return Agent(FunctionModel(respond), tools=[probe]), state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,peak", [("sequential", 1), ("parallel", 3), ("parallel_ordered_events", 3)]
)
async def test_sdk_mode_controls_actual_tool_overlap(mode: str, peak: int) -> None:
    agent, state = probe_agent()
    result = await run_native_agent(
        agent,
        "Probe",
        conversation_id="session",
        run_id="run",
        tool_execution_mode=mode,
    )
    assert result.output == "done"
    assert state == {"active": 0, "peak": peak, "model_calls": 2, "tools": 3}


@pytest.mark.asyncio
async def test_modes_do_not_leak_between_concurrent_runs() -> None:
    serial, serial_state = probe_agent()
    parallel, parallel_state = probe_agent()
    await asyncio.gather(
        run_native_agent(
            serial,
            "Probe",
            conversation_id="s",
            run_id="s",
            tool_execution_mode="sequential",
        ),
        run_native_agent(
            parallel,
            "Probe",
            conversation_id="p",
            run_id="p",
            tool_execution_mode="parallel",
        ),
    )
    assert serial_state["peak"] == 1
    assert parallel_state["peak"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, None, 1, "serial", "", "parallel-secret-value"])
async def test_invalid_mode_refuses_before_any_model_request(mode: Any) -> None:
    agent, state = probe_agent()
    with pytest.raises(
        ValueError, match="Invalid native tool execution mode"
    ) as caught:
        await run_native_agent(
            agent, "Probe", conversation_id="s", run_id="r", tool_execution_mode=mode
        )
    assert "secret-value" not in str(caught.value)
    assert state["model_calls"] == state["tools"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
async def test_failed_run_restores_the_ambient_sdk_mode(failure: Any) -> None:
    async def fail() -> str:
        raise failure()

    agent = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[ToolCallPart("fail", {})])
        ),
        tools=[fail],
    )
    with pytest.raises(failure):
        await run_native_agent(
            agent,
            "Fail",
            conversation_id="s",
            run_id="r",
            tool_execution_mode="sequential",
        )
    subsequent, state = probe_agent()
    await subsequent.run("Probe using the ambient SDK mode")
    assert state["peak"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["serial", "private-setting-value"])
async def test_invalid_mode_refuses_native_action_startup(mode: str) -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    action = NativeHarnessAction(tool_execution_mode=mode)
    with pytest.raises(
        ValueError, match="Invalid native tool execution mode"
    ) as caught:
        await action.on_startup()
    assert mode not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,peak", [("sequential", 1), ("parallel", 3)])
async def test_official_subagent_inherits_host_execution_mode(
    mode: str, peak: int
) -> None:
    from pydantic_ai_harness import SubAgent, SubAgents

    child, state = probe_agent()
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task", {"agent_name": "reader", "task": "Probe"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    parent = Agent(
        FunctionModel(respond),
        capabilities=[
            SubAgents(agents=[SubAgent(child, name="reader", read_only=True)])
        ],
    )
    result = await run_native_agent(
        parent,
        "Delegate",
        conversation_id="session",
        run_id="parent",
        tool_execution_mode=mode,
    )
    assert result.output == "done"
    assert state["tools"] == 3 and state["peak"] == peak
