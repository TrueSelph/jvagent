"""Host resource selection cannot authorize or widen an SDK run."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import run_native_agent
from jvagent.harness.run_limits import effective_run_limits, effective_tool_timeout


def owner(policies: Any) -> Any:
    return SimpleNamespace(
        max_requests=4,
        max_tool_calls=10,
        max_tokens=10000,
        max_output_tokens=None,
        timeout_seconds=10.0,
        max_run_cost_usd="1.00",
        channel_limits=policies,
    )


def test_exact_selection_keeps_base_and_declarations_immutable() -> None:
    policies = {"whatsapp": {"max_requests": 1, "max_run_cost_usd": "0.25"}}
    original = deepcopy(policies)
    action = owner(policies)
    selected = effective_run_limits(action, "whatsapp")
    assert selected["max_requests"] == 1
    assert selected["max_run_cost_usd"] == "0.25"
    assert selected["timeout_seconds"] == 10.0
    for channel in ("default", "whatsapp_call", "whatsapp_other"):
        assert effective_run_limits(action, channel)["max_requests"] == 4
    selected["max_requests"] = 999
    assert policies == original and action.max_requests == 4


def test_tool_deadline_selection_is_separate_from_sdk_run_arguments() -> None:
    action = owner({"voice": {"tool_timeout_seconds": 0.2}})
    assert effective_tool_timeout(action, "voice") == 0.2
    assert effective_tool_timeout(action, "default") is None
    assert "tool_timeout_seconds" not in effective_run_limits(action, "voice")
    action.tool_timeout_seconds = 1.0
    assert effective_tool_timeout(action, "voice") == 0.2
    assert effective_tool_timeout(action, "voice_extra") == 1.0


@pytest.mark.parametrize(
    "value", [0, -1, True, "0.2", None, float("nan"), float("inf"), 10**400]
)
def test_unmatched_invalid_tool_deadline_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        effective_run_limits(
            owner({"voice": {"tool_timeout_seconds": value}}), "default"
        )


def test_channel_cannot_remove_or_widen_base_tool_deadline() -> None:
    action = owner({"voice": {"tool_timeout_seconds": 2.0}})
    action.tool_timeout_seconds = 1.0
    with pytest.raises(ValueError):
        effective_tool_timeout(action, "default")


@pytest.mark.parametrize(
    "policies",
    [
        None,
        [],
        "private-policy",
        {"": {}},
        {" voice ": {}},
        {"voice": None},
        {"voice": []},
        {"voice": {"instructions": "private-policy"}},
        {"voice": {"model": "private-policy"}},
        {"voice": {"max_conversation_cost_usd": "0.25"}},
        {"voice": {"max_requests": True}},
        {"voice": {"max_requests": "1"}},
        {"voice": {"max_requests": 0}},
        {"voice": {"max_requests": 5}},
        {"voice": {"max_tool_calls": 11}},
        {"voice": {"max_tokens": 10001}},
        {"voice": {"max_output_tokens": None}},
        {"voice": {"max_output_tokens": -1}},
        {"voice": {"timeout_seconds": True}},
        {"voice": {"timeout_seconds": float("inf")}},
        {"voice": {"timeout_seconds": 11}},
        {"voice": {"max_run_cost_usd": 0.25}},
        {"voice": {"max_run_cost_usd": "NaN"}},
        {"voice": {"max_run_cost_usd": "1.01"}},
    ],
)
def test_invalid_or_widening_unmatched_policy_fails_closed(policies: Any) -> None:
    with pytest.raises(ValueError) as caught:
        effective_run_limits(owner(policies), "default")
    assert "private-policy" not in str(caught.value)


@pytest.mark.asyncio
async def test_selected_request_limit_is_enforced_by_the_sdk() -> None:
    observed: list[str] = []

    async def read() -> str:
        observed.append("tool")
        return "read"

    def respond(messages: Any, info: Any) -> ModelResponse:
        observed.append("model")
        return ModelResponse(parts=[ToolCallPart("read", {})])

    action = owner({"voice": {"max_requests": 1}})
    limits = effective_run_limits(action, "voice")
    cost = limits.pop("max_run_cost_usd")
    with pytest.raises(UsageLimitExceeded):
        await run_native_agent(
            Agent(FunctionModel(respond), tools=[read]),
            "read twice",
            conversation_id="s",
            run_id="r",
            max_cost_usd=cost,
            **limits,
        )
    assert observed == ["model", "tool"]


@pytest.mark.asyncio
async def test_profiles_stay_local_and_sdk_timeout_cancels_work() -> None:
    cancelled = asyncio.Event()

    async def slow(messages: Any, info: Any) -> ModelResponse:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return ModelResponse(parts=[TextPart("unreachable")])

    action = owner({"voice": {"timeout_seconds": 0.01}})

    async def run(channel: str, agent: Any) -> Any:
        limits = effective_run_limits(action, channel)
        cost = limits.pop("max_run_cost_usd")
        return await run_native_agent(
            agent,
            "hello",
            conversation_id=channel,
            run_id=channel,
            max_cost_usd=cost,
            **limits,
        )

    normal, timeout = await asyncio.gather(
        run(
            "default",
            Agent(FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("ok")]))),
        ),
        run("voice", Agent(FunctionModel(slow))),
        return_exceptions=True,
    )
    assert normal.output == "ok"
    assert isinstance(timeout, TimeoutError) and cancelled.is_set()
    assert action.timeout_seconds == 10.0


@pytest.mark.asyncio
async def test_startup_rejects_an_invalid_unmatched_profile() -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    action = NativeHarnessAction(channel_limits={"voice": {"max_requests": 999}})
    with pytest.raises(ValueError, match="tighten"):
        await action.on_startup()
