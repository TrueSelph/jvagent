"""Tests for the work-graph primitives over the TaskStore (ADR-0026, TP1)."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.task_graph import (
    has_outstanding_work,
    is_runnable,
    pick_top_runnable,
    prerequisites_met,
)
from jvagent.memory.task_store import TaskStore


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


@pytest.mark.asyncio
async def test_dead_prerequisite_cascades_no_zombie(test_db):
    """A cancelled/failed prerequisite abandons its dependents transitively, so a
    dead blocker never leaves a non-terminal-but-unrunnable zombie (which would
    keep the engagement state True forever)."""
    conv, store = await _store()
    try:
        # Chain: gated <- mid <- prereq (gated blocked_on mid, mid blocked_on prereq).
        gated = await store.create(title="gated", description="d", task_type="SKILL")
        await gated.start()
        mid = await store.create(title="mid", description="d", task_type="SKILL")
        await mid.start()
        prereq = await store.create(title="prereq", description="d", task_type="SKILL")
        await prereq.start()
        await gated.add_blocker(mid.id)
        await mid.add_blocker(prereq.id)

        # Abandon the deepest prerequisite → the whole chain is cancelled.
        await TaskStore(conv).get(prereq.id).cancel(reason="user gave up")

        store2 = TaskStore(conv)
        assert store2.get(prereq.id).status == "cancelled"
        assert store2.get(mid.id).status == "cancelled"  # cascaded
        assert store2.get(gated.id).status == "cancelled"  # cascaded transitively
        # No zombie: nothing runnable, and the store is no longer "engaged".
        assert pick_top_runnable(store2) is None
        assert has_outstanding_work(store2) is False
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_completed_prerequisite_does_not_cascade(test_db):
    """The cascade is only for non-completed terminal states — a normally completed
    prerequisite unblocks its dependent, it does not abandon it."""
    conv, store = await _store()
    try:
        gated = await store.create(title="gated", description="d", task_type="SKILL")
        await gated.start()
        prereq = await store.create(title="prereq", description="d", task_type="SKILL")
        await prereq.start()
        await gated.add_blocker(prereq.id)

        await TaskStore(conv).get(prereq.id).complete()
        store2 = TaskStore(conv)
        assert store2.get(gated.id).status == "active"  # not cancelled
        assert is_runnable(store2, store2.get(gated.id)) is True  # now runnable
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_proactive_runnable_only_when_claimed(test_db):
    """Unification (ADR-0026 + 0022): a PROACTIVE task is the scheduler's to
    eligibility-gate. While pending (queued) it is NOT runnable for the generic
    resolver — so a queued proactive task never fires on an ordinary turn. Once the
    scheduler claims it (pending → active) it is a first-class runnable graph task,
    so pick_top_runnable / has_outstanding_work / invariant 7 cover it."""
    conv, store = await _store()
    try:
        p = await store.create(title="ping", description="d", task_type="PROACTIVE")
        # Pending = queued, not due → invisible to the generic resolver.
        assert is_runnable(store, store.get(p.id)) is False
        assert pick_top_runnable(store, task_types=["PROACTIVE"]) is None
        assert has_outstanding_work(store, task_types=["PROACTIVE"]) is False

        # Scheduler claims it (pending → active) → now runnable/visible.
        await store.get(p.id).start()
        assert is_runnable(store, store.get(p.id)) is True
        top = pick_top_runnable(store, task_types=["PROACTIVE"])
        assert top is not None and top.id == p.id
        assert has_outstanding_work(store, task_types=["PROACTIVE"]) is True

        # A SKILL task, by contrast, is runnable while still pending (no claim gate).
        s = await store.create(title="s", description="d", task_type="SKILL")
        assert is_runnable(store, store.get(s.id)) is True
    finally:
        await conv.delete(cascade=True)
