"""TaskMonitor optional ProductAction extraction reaper hook (QUO-2)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _monitor(**kwargs):
    from jvagent.action.task_monitor.task_monitor import TaskMonitor

    defaults = {
        "agent_id": "agent-1",
        "enabled": True,
        "max_parallel_conversations": 1,
        "terminal_ttl_days": 0,
    }
    defaults.update(kwargs)
    return TaskMonitor(**defaults)


@pytest.mark.asyncio
async def test_tick_invokes_product_action_reap_when_present():
    monitor = _monitor()

    product_action = MagicMock()
    product_action.reap_stale_extraction_jobs = AsyncMock(return_value=0)

    agent = MagicMock()
    agent.get_memory = AsyncMock(return_value=MagicMock(id="mem-1"))
    agent.get_action_by_type = AsyncMock(
        side_effect=lambda name: product_action if name == "ProductAction" else None
    )

    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    with (
        patch("jvagent.core.cache.get_cached_agent", AsyncMock(return_value=agent)),
        patch(
            "jvagent.memory.conversation.Conversation.find", AsyncMock(return_value=[])
        ),
        patch("jvagent.core.app.App.get", AsyncMock(return_value=MagicMock())),
        patch("jvagent.core.app.app_now_aware_utc", AsyncMock(return_value=now)),
        patch("jvagent.logging.retention.purge_logs_past_retention", AsyncMock()),
    ):
        result = await monitor.tick(dry_run=False)

    product_action.reap_stale_extraction_jobs.assert_awaited_once()
    assert result is not None


@pytest.mark.asyncio
async def test_tick_skips_reap_when_product_action_absent():
    monitor = _monitor()

    agent = MagicMock()
    agent.get_memory = AsyncMock(return_value=MagicMock(id="mem-1"))
    agent.get_action_by_type = AsyncMock(return_value=None)

    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    with (
        patch("jvagent.core.cache.get_cached_agent", AsyncMock(return_value=agent)),
        patch(
            "jvagent.memory.conversation.Conversation.find", AsyncMock(return_value=[])
        ),
        patch("jvagent.core.app.App.get", AsyncMock(return_value=MagicMock())),
        patch("jvagent.core.app.app_now_aware_utc", AsyncMock(return_value=now)),
        patch("jvagent.logging.retention.purge_logs_past_retention", AsyncMock()),
    ):
        await monitor.tick(dry_run=False)

    # No exception — absent ProductAction is a no-op.
