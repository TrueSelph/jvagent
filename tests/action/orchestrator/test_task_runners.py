"""Task-runner registry + generic store drain (ADR-0026 §2.4/§3, invariant 7)."""

import uuid
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.task_runners import (
    BUILTIN_SKILL_TYPE,
    RunContext,
    TaskRunResult,
    clear_task_runners,
    get_task_runner,
    register_task_runner,
    runnable_task_types,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore


def test_skill_is_always_runnable_even_without_a_registered_runner():
    clear_task_runners()
    try:
        assert BUILTIN_SKILL_TYPE in runnable_task_types()
        assert get_task_runner("SKILL") is None  # advanced by the loop, not a runner
    finally:
        clear_task_runners()


def test_skill_runner_cannot_be_registered():
    clear_task_runners()
    try:
        with pytest.raises(ValueError):
            register_task_runner("skill", lambda ctx: None)
    finally:
        clear_task_runners()


@pytest.mark.asyncio
async def test_register_and_dispatch_a_custom_runner():
    clear_task_runners()
    try:

        async def action_runner(ctx: RunContext) -> TaskRunResult:
            return TaskRunResult(status="completed", directive="done")

        register_task_runner("action", action_runner)
        # Case-insensitive; included alongside the built-in loop-advanced types.
        assert runnable_task_types() == frozenset({"SKILL", "PROACTIVE", "ACTION"})
        runner = get_task_runner("Action")
        assert runner is not None
        result = await runner(RunContext(orchestrator=None, visitor=None, task=None))
        assert result.status == "completed"
        assert result.directive == "done"
    finally:
        clear_task_runners()


async def _visitor_with_store():
    conv = await Conversation.create(
        session_id=f"dr-{uuid.uuid4().hex[:8]}", user_id="u", channel="default"
    )
    return SimpleNamespace(conversation=conv, tasks=TaskStore(conv)), conv


@pytest.mark.asyncio
async def test_drain_dispatches_nonskill_runner_until_drained(
    make_orchestrator, test_db
):
    """The standing drain dispatches non-skill runnable tasks via their runner and
    keeps going until the store is drained — the orchestrator watches the graph
    regardless of any skill turn-lock (ADR-0026 §3)."""
    clear_task_runners()
    visitor, conv = await _visitor_with_store()
    try:
        ex = make_orchestrator(activation_budget=10)
        store = TaskStore(conv)
        t1 = await store.create(title="a1", description="d", task_type="action")
        await t1.start()
        t2 = await store.create(title="a2", description="d", task_type="action")
        await t2.start()

        calls = []

        async def runner(ctx: RunContext) -> TaskRunResult:
            calls.append(ctx.task.title)
            return TaskRunResult(status="completed")

        register_task_runner("action", runner)

        directive = await ex._drain_runnable_tasks(visitor, [])
        assert directive is None  # both dispatched + completed → drained
        assert set(calls) == {"a1", "a2"}
        store2 = TaskStore(conv)
        assert store2.get(t1.id).status == "completed"
        assert store2.get(t2.id).status == "completed"
        # Invariant 7: nothing runnable remains.
        assert await ex._has_runnable_work(visitor) is False
    finally:
        clear_task_runners()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_drain_yields_egress_when_runner_blocks(make_orchestrator, test_db):
    """A runner that blocks on external input yields one egress directive and the
    task stays non-terminal (engaged), matching the drain-loop yield (ADR-0026 §3)."""
    clear_task_runners()
    visitor, conv = await _visitor_with_store()
    try:
        ex = make_orchestrator(activation_budget=10)
        store = TaskStore(conv)
        t = await store.create(title="a", description="d", task_type="action")
        await t.start()

        async def runner(ctx: RunContext) -> TaskRunResult:
            return TaskRunResult(
                status="blocked", directive="Tell the user: need input"
            )

        register_task_runner("action", runner)

        directive = await ex._drain_runnable_tasks(visitor, [])
        assert directive == "Tell the user: need input"
        assert TaskStore(conv).get(t.id).status == "active"  # still engaged
        # Still runnable → invariant 7 keeps the orchestrator engaged.
        assert await ex._has_runnable_work(visitor) is True
    finally:
        clear_task_runners()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_drain_leaves_skill_tasks_to_the_skill_path(make_orchestrator, test_db):
    """SKILL tasks are advanced by the orchestrator loop, not the runner drain — the
    drain returns without touching them."""
    clear_task_runners()
    visitor, conv = await _visitor_with_store()
    try:
        ex = make_orchestrator(activation_budget=10)
        store = TaskStore(conv)
        sk = await store.create(title="s", description="d", task_type="SKILL")
        await sk.start()

        directive = await ex._drain_runnable_tasks(visitor, [])
        assert directive is None
        assert TaskStore(conv).get(sk.id).status == "active"  # untouched
        # A SKILL task is runnable work (the skill path engages it).
        assert await ex._has_runnable_work(visitor) is True
    finally:
        clear_task_runners()
        await conv.delete(cascade=True)
