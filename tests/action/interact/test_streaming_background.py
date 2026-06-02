"""Streaming interact awaits background actions (Lambda-safe)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
