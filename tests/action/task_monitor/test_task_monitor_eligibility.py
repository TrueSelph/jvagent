"""TaskMonitor eligibility integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_eligibility import pick_next_proactive_task
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore


@pytest.mark.asyncio
async def test_schedule_only_task_not_picked_on_user_turn():
    conv = MagicMock()
    conv.tasks = []
    conv.save = AsyncMock()
    store = TaskStore(conv)
    await store.enqueue_proactive(
        ProactiveTaskSpec(
            directive="scheduled ping",
            not_before="2020-01-01T00:00:00+00:00",
            trigger_on="schedule",
        ),
        title="scheduled",
    )
    interaction = MagicMock()
    interaction.utterance = "hello"
    interaction.inner_monologue = ""
    picked = pick_next_proactive_task(
        store,
        interaction=interaction,
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert picked is None
