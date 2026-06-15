"""Tests for the work-graph primitives over the TaskStore (ADR-0026, TP1)."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore
from jvagent.memory.task_graph import (
    has_outstanding_work,
    is_runnable,
    pick_top_runnable,
    prerequisites_met,
)


def _sid():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


async def _store():
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    return conv, TaskStore(conv)


@pytest.mark.asyncio
async def test_graph_fields_round_trip(test_db):
    conv, store = await _store()
    try:
        h = await store.create(
            title="t",
            description="t",
            task_type="SKILL",
            blocked_on=["task_dep"],
            resumes="task_parent",
            order=3,
            seed={"utterance": "hi"},
            snapshot={"fields": {"a": "1"}},
        )
        # Persisted + reloaded from the conversation node.
        reloaded = TaskStore(conv).get(h.id)
        assert reloaded.blocked_on == ["task_dep"]
        assert reloaded.resumes == "task_parent"
        assert reloaded.order == 3
        assert reloaded.seed == {"utterance": "hi"}
        assert reloaded.snapshot == {"fields": {"a": "1"}}
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_prerequisites_and_runnable(test_db):
    conv, store = await _store()
    try:
        dep = await store.create(title="dep", description="dep", task_type="SKILL")
        await dep.start()
        gated = await store.create(
            title="gated", description="gated", task_type="SKILL", blocked_on=[dep.id]
        )
        await gated.start()

        # dep has no blockers → runnable; gated is blocked on an active dep.
        assert prerequisites_met(store, dep) is True
        assert is_runnable(store, dep) is True
        assert prerequisites_met(store, gated) is False
        assert is_runnable(store, gated) is False

        # Completing dep unblocks gated.
        await dep.complete()
        gated2 = TaskStore(conv).get(gated.id)
        assert prerequisites_met(store, gated2) is True
        assert is_runnable(store, gated2) is True
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_pick_top_runnable_stack_unwinds(test_db):
    conv, store = await _store()
    try:
        # parent ← (blocked_on) prereq : a prerequisite pushed under an active parent.
        parent = await store.create(
            title="parent", description="parent", task_type="SKILL"
        )
        await parent.start()
        prereq = await store.create(
            title="prereq",
            description="prereq",
            task_type="SKILL",
            resumes=parent.id,
        )
        await prereq.start()
        await parent.add_blocker(prereq.id)

        # Only the unblocked prerequisite is runnable.
        top = pick_top_runnable(store)
        assert top is not None and top.id == prereq.id

        # Completing it → the parent resumes deterministically (no model involvement).
        await prereq.complete()
        top2 = pick_top_runnable(store)
        assert top2 is not None and top2.id == parent.id
        assert top2.resumes is None  # parent is the root
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_outstanding_work_drives_engagement(test_db):
    conv, store = await _store()
    try:
        assert has_outstanding_work(store) is False
        h = await store.create(title="t", description="t", task_type="SKILL")
        await h.start()
        assert has_outstanding_work(store) is True
        assert has_outstanding_work(store, task_types=["SKILL"]) is True
        assert has_outstanding_work(store, task_types=["PROACTIVE"]) is False
        # Draining to terminal ⇒ no outstanding work ⇒ orchestrator may go idle.
        await h.complete()
        assert has_outstanding_work(store) is False
    finally:
        await conv.delete(cascade=True)
