"""Tests for TaskStore proactive queue API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore


def _make_store():
    conv = MagicMock()
    conv.tasks = []
    conv.save = AsyncMock()
    return TaskStore(conv), conv


@pytest.mark.asyncio
async def test_enqueue_proactive_creates_pending_task():
    store, conv = _make_store()
    spec = ProactiveTaskSpec(directive="Check in")
    handle = await store.enqueue_proactive(
        spec, owner_action="TestAction", title="Check in"
    )
    assert handle.status == "pending"
    assert handle.task_type == "PROACTIVE"
    assert handle.data["spec_version"] == 2
    assert len(conv.tasks) == 1


@pytest.mark.asyncio
async def test_claim_and_requeue_proactive():
    store, _conv = _make_store()
    handle = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="work"),
        title="work",
    )
    assert await store.claim_proactive(handle.id, "lease-1") is True
    refreshed = store.get(handle.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.data["dispatch_lease_id"] == "lease-1"

    assert await store.requeue_proactive(handle.id, "retry") is True
    requeued = store.get(handle.id)
    assert requeued is not None
    assert requeued.status == "pending"
    assert requeued.data["attempt_count"] == 1


@pytest.mark.asyncio
async def test_list_queue_filters_statuses():
    store, _conv = _make_store()
    pending = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="a"),
        title="a",
    )
    active = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="b"),
        title="b",
    )
    await store.claim_proactive(active.id, "lease-2")
    ids = {h.id for h in store.list_queue(statuses=("pending",))}
    assert pending.id in ids
    assert active.id not in ids
