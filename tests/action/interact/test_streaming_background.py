"""Streaming interact awaits background actions (Lambda-safe)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from jvagent.action.interact.endpoints import (
    _run_background_actions,
    _wait_for_stream_message,
)


async def test_streaming_path_awaits_background_actions_not_fire_and_forget():
    """Regression: streaming must await _run_background_actions like non-streaming."""
    import inspect

    from jvagent.action.interact import endpoints

    source = inspect.getsource(endpoints._stream_interaction)
    assert "create_task(" not in source or "_run_background_actions" in source
    # Explicit await path must exist (no fire-and-forget task for background work).
    assert "await _run_background_actions(walker)" in source


async def test_streaming_path_emits_heartbeats_during_idle_model_calls():
    """Keep a slow but active model call alive across idle connection limits."""
    import inspect

    from jvagent.action.interact import endpoints

    source = inspect.getsource(endpoints._stream_interaction)
    assert '"type": "heartbeat"' in source
    assert "_wait_for_stream_message(" in source
    helper = inspect.getsource(endpoints._wait_for_stream_message)
    assert "poll_interval: float = 0.25" in helper
    assert "heartbeat_interval: float = 15.0" in helper


async def test_stream_message_wait_reports_heartbeat_after_idle_interval():
    queue = asyncio.Queue()
    message, heartbeat_due, next_heartbeat = await _wait_for_stream_message(
        queue,
        time.monotonic() - 1,
        poll_interval=0.001,
        heartbeat_interval=0,
    )

    assert message is None
    assert heartbeat_due is True
    assert next_heartbeat > 0


async def test_stream_message_wait_preserves_queued_messages():
    queue = asyncio.Queue()
    await queue.put("response")
    last_heartbeat = time.monotonic()

    message, heartbeat_due, next_heartbeat = await _wait_for_stream_message(
        queue, last_heartbeat, heartbeat_interval=0
    )

    assert message == "response"
    assert heartbeat_due is False
    assert next_heartbeat == last_heartbeat


async def test_run_background_actions_executes_deferred_actions():
    action = MagicMock()
    action.execute = AsyncMock()
    walker = MagicMock()
    walker.background_actions = [action]
    walker.enforce_interact_action_access = AsyncMock(return_value=True)

    await _run_background_actions(walker)

    action.execute.assert_awaited_once_with(walker)


async def test_background_actions_bind_interaction_to_context_for_observability(
    monkeypatch,
):
    """Regression: background InteractActions make model calls (e.g. long-memory
    assimilation) AFTER the turn cleared the interaction from context. Without
    re-binding it, ``track_usage`` sees no interaction and drops their
    ``model_call`` events from ``observability_metrics`` — so jvchat's Debug view
    never showed them. The runner must bind the interaction during execution and
    clear it afterward.
    """
    from jvagent.action.interact import endpoints
    from jvagent.action.model.context import get_interaction, set_interaction

    captured = {}

    class FakeInteraction:
        def __init__(self):
            self.observability_metrics = []

    interaction = FakeInteraction()

    class BgAction:
        def get_class_name(self):
            return "BgAction"

        async def execute(self, walker):
            # Simulate a model call recording observability via context, the way
            # BaseModelAction.track_usage does.
            captured["during"] = get_interaction()
            ix = get_interaction()
            if ix is not None:
                ix.observability_metrics.append({"event_type": "model_call"})

    walker = MagicMock()
    walker.interaction = interaction
    walker.background_actions = [BgAction()]
    walker.enforce_interact_action_access = AsyncMock(return_value=True)

    finalize = AsyncMock()
    monkeypatch.setattr(
        "jvagent.action.interact.webhook_pipeline.finalize_usage", finalize
    )

    # Post-turn state: context already cleared by the interact/stream handler.
    set_interaction(None)
    try:
        await _run_background_actions(walker)

        # Bound to the turn's interaction while the background action ran...
        assert captured["during"] is interaction
        # ...and the model call landed in observability_metrics.
        assert interaction.observability_metrics == [{"event_type": "model_call"}]
        # Usage recomputed so the persisted interaction reflects the new calls.
        finalize.assert_awaited_once_with(interaction)
        # Context cleared afterward (no leak into later turns on this task).
        assert get_interaction() is None
    finally:
        set_interaction(None)
