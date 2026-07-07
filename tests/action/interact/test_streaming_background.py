"""Streaming interact awaits background actions (Lambda-safe)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interact.endpoints import _run_background_actions

pytestmark = pytest.mark.asyncio


async def test_streaming_path_awaits_background_actions_not_fire_and_forget():
    """Regression: streaming must await _run_background_actions like non-streaming."""
    import inspect

    from jvagent.action.interact import endpoints

    source = inspect.getsource(endpoints._stream_interaction)
    assert "create_task(" not in source or "_run_background_actions" in source
    # Explicit await path must exist (no fire-and-forget task for background work).
    assert "await _run_background_actions(walker)" in source


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
    monkeypatch.setattr(endpoints, "_finalize_usage", finalize)

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
