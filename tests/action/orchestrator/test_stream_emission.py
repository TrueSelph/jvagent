"""Orchestrator streaming-emission shapes (orchestrator-stream-emission-spec).

Producer-side envelopes the chat UI / translator consumes:
- §A acks are channel-conditional: thought/status on a streamed UI (ephemeral
  activity strip), a whole category="user" message on a non-streamed channel
  (delivered by the channel adapter — e.g. WhatsApp).
- §B reasoning thoughts carry thought_type="reasoning".
- §C tool dispatch emits structured tool_call/tool_result thoughts sharing one
  segment_id, with tool_name/tool_args/tool_result/is_error metadata.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


class _FakeBus:
    def __init__(self) -> None:
        self.published: List[Dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


def _visitor(*, stream: bool = False, bus: Any = None) -> Any:
    return SimpleNamespace(
        response_bus=bus,
        session_id="sess1",
        channel="default",
        stream=stream,
        interaction=SimpleNamespace(id="int1", user_id="u1"),
    )


# --- §B reasoning -----------------------------------------------------------


async def test_emit_thought_tags_reasoning():
    bus = _FakeBus()
    await OrchestratorInteractAction()._emit_thought(_visitor(bus=bus), "thinking…")
    assert len(bus.published) == 1
    p = bus.published[0]
    assert p["category"] == "thought"
    assert p["thought_type"] == "reasoning"
    assert p["transient"] is True


# --- §C tool_call / tool_result ---------------------------------------------


async def test_emit_tool_thought_call_then_result_share_segment():
    bus = _FakeBus()
    ex = OrchestratorInteractAction()
    v = _visitor(bus=bus)
    await ex._emit_tool_thought(v, "tool_call", "filing__create", "seg1", args={"x": 1})
    await ex._emit_tool_thought(v, "tool_result", "filing__create", "seg1", obs="done")
    call, result = bus.published
    assert call["category"] == "thought" and call["thought_type"] == "tool_call"
    assert call["segment_id"] == "seg1" and call["transient"] is True
    assert call["metadata"] == {"tool_name": "filing__create", "tool_args": {"x": 1}}
    assert result["thought_type"] == "tool_result" and result["segment_id"] == "seg1"
    assert result["metadata"]["tool_name"] == "filing__create"
    assert result["metadata"]["tool_result"] == "done"
    assert result["metadata"]["is_error"] is False


async def test_emit_tool_thought_flags_errors():
    bus = _FakeBus()
    ex = OrchestratorInteractAction()
    await ex._emit_tool_thought(
        _visitor(bus=bus), "tool_result", "t", "s", obs="(tool error: boom)"
    )
    assert bus.published[0]["metadata"]["is_error"] is True


async def test_emit_tool_thought_uncapped_by_default():
    # Default (tool_thought_max_chars=0) = NO CAP: the full tool result is sent
    # so structured JSON results stay COMPLETE and parseable in the UI rather
    # than being cut mid-value into invalid JSON.
    bus = _FakeBus()
    ex = OrchestratorInteractAction()
    big = "x" * 50000
    await ex._emit_tool_thought(_visitor(bus=bus), "tool_result", "t", "s", obs=big)
    p = bus.published[0]
    assert len(p["content"]) == 50000
    assert len(p["metadata"]["tool_result"]) == 50000


async def test_emit_tool_thought_truncates_at_configured_cap():
    # The cap is configurable; when a result exceeds it, the bus envelope is
    # truncated (the model still sees the full observation elsewhere).
    bus = _FakeBus()
    ex = OrchestratorInteractAction(tool_thought_max_chars=2000)
    big = "x" * 5000
    await ex._emit_tool_thought(_visitor(bus=bus), "tool_result", "t", "s", obs=big)
    p = bus.published[0]
    assert len(p["content"]) == 2000
    assert len(p["metadata"]["tool_result"]) == 2000


async def test_emit_tool_thought_no_cap_when_zero():
    bus = _FakeBus()
    ex = OrchestratorInteractAction(tool_thought_max_chars=0)
    big = "x" * 5000
    await ex._emit_tool_thought(_visitor(bus=bus), "tool_result", "t", "s", obs=big)
    p = bus.published[0]
    assert len(p["content"]) == 5000


async def test_emit_tool_thought_noop_without_bus():
    ex = OrchestratorInteractAction()
    # No bus → best-effort no-op (must not raise).
    await ex._emit_tool_thought(_visitor(bus=None), "tool_call", "t", "s", args={})


# --- §A channel-conditional acks --------------------------------------------


def _arm_ack(ex: OrchestratorInteractAction) -> None:
    ex.enable_transient_ack = True
    ex.ack_statements = ["One moment…"]
    ex.first_emit_timeout_ms = 0
    ex.ack_interval_ms = 0


async def test_ack_streamed_ui_is_ephemeral_thought_status():
    ex = OrchestratorInteractAction()
    _arm_ack(ex)
    bus = _FakeBus()
    task = ex._schedule_first_emit_ack(_visitor(stream=True, bus=bus))
    assert task is not None
    await task
    p = bus.published[0]
    assert p["category"] == "thought"
    assert p["thought_type"] == "status"
    assert p["transient"] is True


async def test_ack_non_streamed_channel_is_whole_user_message():
    ex = OrchestratorInteractAction()
    _arm_ack(ex)
    bus = _FakeBus()
    task = ex._schedule_first_emit_ack(_visitor(stream=False, bus=bus))
    assert task is not None
    await task
    p = bus.published[0]
    # Whole, deliverable message for non-streamed channels (WhatsApp): a channel
    # adapter relays category="user"; transient keeps it out of the persisted
    # answer. No thought_type (not an activity-strip line).
    assert p["category"] == "user"
    assert "thought_type" not in p
    assert p["transient"] is True
