"""A stale StepHandle must not resurrect a zombie step (AUDIT-memory M15).

set_plan / sync_plan regenerate all step ids. A StepHandle captured from a prior
plan then completing/failing/updating must NOT re-append its (now-orphaned) step
into the new plan — has_pending_steps / current_step would count it as phantom
work and mis-plan a resumed flow."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.memory.task_store import TaskStore

pytestmark = pytest.mark.asyncio


class FakeConversation:
    def __init__(self) -> None:
        self.tasks: List[Dict[str, Any]] = []

    async def save(self) -> None:
        pass


async def _task_with_plan():
    conv = FakeConversation()
    store = TaskStore(conv)
    handle = await store.create(
        title="t", description="t", task_type="AGENTIC_LOOP", owner_action="O"
    )
    await handle.start()
    return store, handle


async def test_stale_step_complete_does_not_append_zombie():
    store, handle = await _task_with_plan()
    old_steps = await handle.set_plan(["step A", "step B"])
    stale = old_steps[0]  # handle into the OLD plan

    # Regenerate the plan — all step ids change.
    await handle.sync_plan([{"description": "step X", "status": "pending"}])

    # The stale handle completes; it must be dropped, not resurrected.
    await stale.complete(result="done")

    fresh = store.get(handle.id)
    descs = [s.description for s in fresh.list_steps()]
    assert descs == ["step X"], descs


async def test_stale_step_update_does_not_pollute_pending():
    store, handle = await _task_with_plan()
    old_steps = await handle.set_plan(["a", "b", "c"])
    stale = old_steps[2]

    await handle.sync_plan([{"description": "only", "status": "in_progress"}])
    await stale.update(status="failed")

    fresh = store.get(handle.id)
    # Only the new in-progress step is present; the stale 'failed' step is gone.
    assert [s.description for s in fresh.list_steps()] == ["only"]
    pending = [s.description for s in fresh.pending_steps()]
    assert pending == ["only"]


async def test_live_step_update_still_persists():
    """The fix must not break updates to a current (non-stale) step."""
    store, handle = await _task_with_plan()
    steps = await handle.set_plan(["a", "b"])

    await steps[0].complete(result="ok")

    fresh = store.get(handle.id)
    by_desc = {s.description: s for s in fresh.list_steps()}
    assert by_desc["a"].status == "done"
    assert by_desc["a"].result == "ok"
