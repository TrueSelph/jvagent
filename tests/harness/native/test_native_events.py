"""Provider reasoning is opt-in, bounded and separate from tool lifecycle."""

from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel

from jvagent.action.native_harness.native_harness_action import publish_native_event


def context() -> tuple[Any, Any, Any]:
    visitor = SimpleNamespace(
        response_bus=SimpleNamespace(publish=AsyncMock()),
        session_id="session",
        user_id="caller",
        channel="web",
    )
    return (
        visitor,
        SimpleNamespace(id="interaction"),
        SimpleNamespace(run_id="run", agent=SimpleNamespace(name="child")),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failed", "retried"])
async def test_sdk_tool_outcome_reaches_the_view_without_text_inference(
    outcome: Any,
) -> None:
    visitor, interaction, ctx = context()
    await publish_native_event(
        visitor,
        interaction,
        ctx,
        FunctionToolResultEvent(
            ToolReturnPart(
                tool_name="lookup",
                content="A result",
                tool_call_id="call",
                outcome=outcome,
            )
        ),
        publish_reasoning=False,
    )
    receipt = visitor.response_bus.publish.await_args.kwargs
    assert receipt["metadata"]["is_error"] is (outcome != "success")
    assert receipt["metadata"]["tool_result"] == "A result"


@pytest.mark.asyncio
async def test_public_retry_prompt_part_is_presented_as_failure() -> None:
    visitor, interaction, ctx = context()
    await publish_native_event(
        visitor,
        interaction,
        ctx,
        FunctionToolResultEvent(
            RetryPromptPart(
                content="Try again",
                tool_name="lookup",
                tool_call_id="call",
            )
        ),
        publish_reasoning=False,
    )
    assert (
        visitor.response_bus.publish.await_args.kwargs["metadata"]["is_error"] is True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_reasoning_policy_and_tool_events_are_independent(enabled: bool) -> None:
    visitor, interaction, ctx = context()
    part = ThinkingPart(
        "Provider supplied summary",
        signature="opaque-provider-signature",
        provider_details={"private": "opaque-metadata"},
    )
    for event in (
        PartStartEvent(index=0, part=part),
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="delta")),
        PartEndEvent(index=0, part=part),
        PartEndEvent(index=1, part=TextPart("Final answer")),
        FunctionToolCallEvent(ToolCallPart("lookup", {"query": "public"}, "call")),
    ):
        await publish_native_event(
            visitor, interaction, ctx, event, publish_reasoning=enabled, proactive=True
        )
    published = [c.kwargs for c in visitor.response_bus.publish.await_args_list]
    assert [p["thought_type"] for p in published] == (
        ["reasoning", "tool_call"] if enabled else ["tool_call"]
    )
    assert all(p["interaction"] is interaction for p in published)
    assert all(p["metadata"]["native_run_id"] == "run" for p in published)
    assert all(p["metadata"]["native_agent_name"] == "child" for p in published)
    assert all(p["metadata"]["is_proactive"] for p in published)
    assert "opaque" not in str(published)
    if enabled:
        assert published[0]["content"] == part.content
        assert published[0]["metadata"]["native_reasoning_source"] == "provider"
        assert published[0]["metadata"]["truncated"] is False
        assert published[0]["segment_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "  ", "x" * 9000])
async def test_empty_opaque_reasoning_and_display_bound(content: str) -> None:
    visitor, interaction, ctx = context()
    await publish_native_event(
        visitor,
        interaction,
        ctx,
        PartEndEvent(index=0, part=ThinkingPart(content, signature="not-displayable")),
        publish_reasoning=True,
    )
    if content.strip():
        published = visitor.response_bus.publish.await_args.kwargs
        assert published["content"] == "x" * 8000
        assert published["metadata"]["truncated"] is True
    else:
        visitor.response_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_public_sdk_stream_projects_each_completed_part_once(
    enabled: bool,
) -> None:
    visitor, interaction, _ = context()

    async def stream(messages: Any, info: Any) -> AsyncIterator[Any]:
        yield {0: DeltaThinkingPart(content="Provider ")}
        yield {0: DeltaThinkingPart(content="summary", signature="opaque-signature")}
        yield "Complete"

    async def events(ctx: Any, stream: Any) -> None:
        async for event in stream:
            await publish_native_event(
                visitor, interaction, ctx, event, publish_reasoning=enabled
            )

    agent = Agent(FunctionModel(stream_function=stream), name="primary")
    result = await agent.run("Hello", event_stream_handler=events)
    assert result.output == "Complete"
    published = visitor.response_bus.publish.await_args_list
    assert len(published) == int(enabled)
    if enabled:
        assert published[0].kwargs["content"] == "Provider summary"
        assert "opaque" not in str(published)


@pytest.mark.asyncio
async def test_interrupted_reasoning_is_not_published_as_complete() -> None:
    visitor, interaction, _ = context()

    async def stream(messages: Any, info: Any) -> AsyncIterator[Any]:
        yield {0: DeltaThinkingPart(content="Incomplete provider text")}
        raise RuntimeError("Synthetic provider interruption")

    async def events(ctx: Any, stream: Any) -> None:
        async for event in stream:
            await publish_native_event(
                visitor, interaction, ctx, event, publish_reasoning=True
            )

    agent = Agent(FunctionModel(stream_function=stream))
    with pytest.raises(RuntimeError, match="Synthetic provider interruption"):
        await agent.run("Hello", event_stream_handler=events)
    visitor.response_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [0, 1, "false", None, []])
async def test_reasoning_startup_rejects_untyped_values(invalid: Any) -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    action = NativeHarnessAction()
    object.__setattr__(action, "publish_reasoning", invalid)
    with pytest.raises(ValueError, match="publish_reasoning must be a boolean"):
        await action.on_startup()
