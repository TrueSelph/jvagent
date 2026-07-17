"""TaskMonitor dry-run must not complete() the picked task (AUDIT-memory M13).

pick_next_proactive_task returns a PENDING handle. The old dry-run path called
handle.complete() on it, which Task.transition rejects (pending -> completed);
the error was swallowed as "dispatch failed" so dry-run never counted anything
and logged an error every tick. This guards the state-machine invariant the fix
relies on: a pending proactive task is not directly completable, so the dry-run
path must leave it untouched."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_eligibility import pick_next_proactive_task
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore

pytestmark = pytest.mark.asyncio


async def _store_with_pending_proactive():
    conv = MagicMock()
    conv.tasks = []
    conv.save = AsyncMock()
    store = TaskStore(conv)
    await store.enqueue_proactive(
        ProactiveTaskSpec(directive="ping"), title="ping"
    )
    return store


async def test_picked_task_is_pending():
    store = await _store_with_pending_proactive()
    picked = pick_next_proactive_task(
        store, now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    assert picked is not None
    assert picked.status == "pending"


async def test_completing_pending_proactive_raises():
    """The exact failure the old dry-run hit: pending -> completed is rejected."""
    store = await _store_with_pending_proactive()
    picked = pick_next_proactive_task(
        store, now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    assert picked is not None
    with pytest.raises(Exception):
        await picked.complete(result="dry-run")

    # The task remains pending — the dry-run path (no complete) leaves it so it
    # is still dispatchable on a real tick.
    assert picked.status == "pending"
