"""Completed channel history is a graph read, never a model/effect replay."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai_harness.step_persistence import RunRecord, StepEvent

from jvagent.action.response.native_replay import replay_completed_replies
from jvagent.action.response.streaming import format_sse_chunk, stream_messages
from jvagent.harness.step_store import InteractionStepStore


async def fixture() -> tuple[Any, Any]:
    row = SimpleNamespace(
        id="interaction",
        conversation_id="conversation",
        session_id="session",
        user_id="caller",
        native_run={
            "status": "delivered",
            "run_id": "run",
            "delivery": {
                "version": 1,
                "message_id": "o.ResponseMessage.run",
                "channel": "default",
                "metadata": {
                    "is_proactive": False,
                    "observability": {"usage": {"total_tokens": 12}},
                    "host_system_context": "must not be projected",
                },
            },
        },
        native_steps={},
        save=AsyncMock(),
        closed=True,
        has_emitted=lambda: True,
        response="Approved answer",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        agent_trace=[
            {
                "message_id": "o.ResponseMessage.tool",
                "thought_type": "tool_call",
                "segment_id": "call",
                "content": "lookup",
                "started_at": "2026-11-05T00:00:00Z",
            }
        ],
    )
    store = InteractionStepStore(row)
    await store.register_run(RunRecord(run_id="run", conversation_id="session"))
    await store.append_event(
        StepEvent(run_id="run", kind="run_completed", step_index=1)
    )
    conversation = SimpleNamespace(
        id="conversation",
        session_id="session",
        user_id="caller",
        get_interactions=AsyncMock(return_value=[row]),
    )
    return conversation, row


@pytest.mark.asyncio
async def test_replay_has_stable_ids_governed_text_trace_and_usage() -> None:
    conversation, row = await fixture()
    row.native_run["output"] = "Ungoverned model output must not replace response"
    frames = [x async for x in replay_completed_replies(conversation, user_id="caller")]
    assert [x["id"] for x in frames] == [
        "o.ResponseMessage.tool",
        "o.ResponseMessage.run",
    ]
    assert frames[-1]["content"] == "Approved answer"
    assert frames[-1]["metadata"]["observability"]["usage"]["total_tokens"] == 12
    assert "must not be projected" not in str(frames)
    assert "Approved answer" in format_sse_chunk(frames[-1])
    assert frames[-1]["timestamp"].endswith("+00:00")
    assert frames == [
        x async for x in replay_completed_replies(conversation, user_id="caller")
    ]
    conversation.get_interactions.assert_awaited_with(limit=20, reverse=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "caller",
        "interaction_owner",
        "session",
        "conversation",
        "running",
        "pending_delivery",
        "delivery_unknown",
        "cancelled",
        "failed",
        "open",
        "unemitted",
        "old_receipt",
        "message_id",
        "missing_registration",
        "child_registration",
        "foreign_registration",
        "nonterminal",
    ],
)
async def test_uncertain_or_foreign_history_is_not_replayed(invalid: str) -> None:
    conversation, row = await fixture()
    user_id = "caller"
    if invalid == "caller":
        user_id = "foreign"
    elif invalid == "interaction_owner":
        row.user_id = "foreign"
    elif invalid == "session":
        row.session_id = "foreign"
    elif invalid == "conversation":
        row.conversation_id = "foreign"
    elif invalid in {
        "running",
        "pending_delivery",
        "delivery_unknown",
        "cancelled",
        "failed",
    }:
        row.native_run["status"] = invalid
    elif invalid == "open":
        row.closed = False
    elif invalid == "unemitted":
        row.has_emitted = lambda: False
    elif invalid == "old_receipt":
        row.native_run.pop("delivery")
    elif invalid == "message_id":
        row.native_run["delivery"]["message_id"] = "foreign"
    elif invalid == "missing_registration":
        row.native_steps["runs"] = []
    elif invalid == "child_registration":
        row.native_steps["runs"][0]["parent_run_id"] = "other"
    elif invalid == "foreign_registration":
        row.native_steps["runs"][0]["conversation_id"] = "foreign"
    elif invalid == "nonterminal":
        row.native_steps["events"] = []
    assert [
        x async for x in replay_completed_replies(conversation, user_id=user_id)
    ] == []


@pytest.mark.asyncio
async def test_graph_and_live_overlap_are_deduplicated_after_subscribing() -> None:
    conversation, _ = await fixture()
    frames = [x async for x in replay_completed_replies(conversation, user_id="caller")]
    subscribed = False

    async def subscribe(*args: Any, **kwargs: Any) -> None:
        nonlocal subscribed
        subscribed = True

    async def history() -> Any:
        assert subscribed
        for frame in frames:
            yield frame

    bus = SimpleNamespace(
        subscribe=subscribe,
        unsubscribe=AsyncMock(),
        get_messages=AsyncMock(
            return_value=[
                SimpleNamespace(**frame, to_dict=lambda value=frame: value)
                for frame in frames
            ]
        ),
    )
    source = stream_messages("session", bus, history=history(), keepalive_seconds=0.001)
    assert "o.ResponseMessage.tool" in await anext(source)
    assert "Approved answer" in await anext(source)
    assert "keepalive" in await anext(source)
    await source.aclose()
    bus.unsubscribe.assert_awaited_once()
