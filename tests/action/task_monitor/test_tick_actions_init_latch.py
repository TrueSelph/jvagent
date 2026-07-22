"""TaskMonitor.tick initializes actions at most once per process.

App.initialize_actions() fans out to every action's on_startup(), whose side
effects are not idempotent (fresh channel-filter instances, WhatsApp session
registration, interview spec re-discovery). Re-running it on every tick with
eligible conversations churned all of that every 2 minutes — and leaked
duplicate channel filters without bound before register_channel_filter deduped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.task_monitor import task_monitor as tm


def _monitor(**kwargs):
    defaults = {
        "agent_id": "agent-1",
        "enabled": True,
        "max_parallel_conversations": 1,
        "terminal_ttl_days": 0,
    }
    defaults.update(kwargs)
    return tm.TaskMonitor(**defaults)


@pytest.mark.asyncio
async def test_tick_initializes_actions_once_across_ticks():
    monitor = _monitor()

    memory = MagicMock(id="mem-1")
    agent = MagicMock()
    agent.get_memory = AsyncMock(return_value=memory)
    agent.get_action_by_type = AsyncMock(return_value=None)

    user = MagicMock(memory_id="mem-1")
    conv = MagicMock(id="conv-1")
    conv.node = AsyncMock(return_value=user)

    app = MagicMock()
    app.initialize_actions = AsyncMock(return_value={})

    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    original = tm._TICK_ACTIONS_INITIALIZED
    tm._TICK_ACTIONS_INITIALIZED = False
    try:
        with (
            patch("jvagent.core.cache.get_cached_agent", AsyncMock(return_value=agent)),
            patch(
                "jvagent.memory.conversation.Conversation.find",
                AsyncMock(return_value=[conv]),
            ),
            # Dispatch returns early on a missing conversation — the latch is
            # what's under test, not the dispatch pipeline.
            patch(
                "jvagent.memory.conversation.Conversation.get",
                AsyncMock(return_value=None),
            ),
            patch("jvagent.core.app.App.get", AsyncMock(return_value=app)),
            patch("jvagent.core.app.app_now_aware_utc", AsyncMock(return_value=now)),
            patch("jvagent.logging.retention.purge_logs_past_retention", AsyncMock()),
        ):
            await monitor.tick(dry_run=False)
            await monitor.tick(dry_run=False)
            await monitor.tick(dry_run=False)

        app.initialize_actions.assert_awaited_once()
        assert tm._TICK_ACTIONS_INITIALIZED is True
    finally:
        tm._TICK_ACTIONS_INITIALIZED = original
