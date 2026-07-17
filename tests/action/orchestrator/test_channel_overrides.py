"""Tests for per-channel loop overrides and voice-shaped transient acks."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


def _visitor(channel: str = "default", stream: bool = False) -> MagicMock:
    visitor = MagicMock()
    visitor.channel = channel
    visitor.stream = stream
    visitor.session_id = "sess_test"
    visitor.interaction = None
    return visitor


def test_channel_cfg_returns_override_for_matching_channel():
    orch = OrchestratorInteractAction()
    orch.channel_overrides = {
        "whatsapp_call": {"history_limit": 6, "activation_budget": 8}
    }
    visitor = _visitor(channel="whatsapp_call")
    assert orch._channel_cfg(visitor, "history_limit", 10) == 6
    assert orch._channel_cfg(visitor, "activation_budget", 40) == 8


def test_channel_cfg_falls_back_without_match():
    orch = OrchestratorInteractAction()
    orch.channel_overrides = {"whatsapp_call": {"history_limit": 6}}
    # Different channel → action-level value.
    assert orch._channel_cfg(_visitor(channel="whatsapp"), "history_limit", 10) == 10
    # Matching channel, unknown key → action-level value.
    assert (
        orch._channel_cfg(_visitor(channel="whatsapp_call"), "max_duration_seconds", 0)
        == 0
    )
    # No overrides configured at all.
    orch.channel_overrides = {}
    assert orch._channel_cfg(_visitor(channel="whatsapp_call"), "history_limit", 4) == 4


@pytest.mark.asyncio
async def test_ack_is_user_category_on_spoken_channel_even_when_streamed():
    """whatsapp_call streams SSE, but the ack must be a speakable user message."""
    orch = OrchestratorInteractAction()
    orch.enable_transient_ack = True
    orch.first_emit_timeout_ms = 0
    orch.ack_statements = ["One moment…"]

    visitor = _visitor(channel="whatsapp_call", stream=True)
    visitor.response_bus = MagicMock()
    visitor.response_bus.publish = AsyncMock()

    task = orch._schedule_first_emit_ack(visitor)
    assert task is not None
    await asyncio.wait_for(task, timeout=2)

    kwargs = visitor.response_bus.publish.await_args.kwargs
    assert kwargs["category"] == "user"
    assert kwargs["transient"] is True
    assert kwargs["content"] == "One moment…"


@pytest.mark.asyncio
async def test_ack_stays_thought_on_streamed_non_voice_channel():
    orch = OrchestratorInteractAction()
    orch.enable_transient_ack = True
    orch.first_emit_timeout_ms = 0
    orch.ack_statements = ["One moment…"]

    visitor = _visitor(channel="default", stream=True)
    visitor.response_bus = MagicMock()
    visitor.response_bus.publish = AsyncMock()

    task = orch._schedule_first_emit_ack(visitor)
    assert task is not None
    await asyncio.wait_for(task, timeout=2)

    kwargs = visitor.response_bus.publish.await_args.kwargs
    assert kwargs["category"] == "thought"
