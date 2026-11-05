"""InteractWalker spawn/finalize semantics (C2-related)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interact.interact_walker import InteractWalker


@pytest.mark.asyncio
async def test_spawn_finalizes_after_queue_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walker = InteractWalker(agent_id="n.Agent.spawn")
    walker.conversation = SimpleNamespace(id="n.Conversation.c1")
    walker.interaction = SimpleNamespace(id="n.Interaction.i1")
    walker.response_bus = SimpleNamespace(finalize_interaction=AsyncMock())
    walker.queue._backing = []
    walker.queue.append = AsyncMock()

    run_order = []

    async def _run() -> None:
        run_order.append("run")

    async def _finalize() -> None:
        run_order.append("finalize")

    walker.run = AsyncMock(side_effect=_run)
    walker._execute_exit_hooks = AsyncMock()
    walker._finalize = AsyncMock(side_effect=_finalize)

    lock_entered = []

    class _Lock:
        async def __aenter__(self) -> None:
            lock_entered.append(True)

        async def __aexit__(self, *args: Any) -> bool:
            return False

    monkeypatch.setattr(
        "jvagent.memory.distributed_conversation_lock.conversation_mutation_lock",
        lambda _cid: _Lock(),
    )
    monkeypatch.setattr(
        "jvspatial.core.events.event_bus.register_entity",
        AsyncMock(),
    )

    agent = SimpleNamespace(id="n.Agent.spawn")
    await walker.spawn(agent)

    assert lock_entered
    assert run_order == ["run", "finalize"]
    walker._finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawn_creates_interaction_inside_conversation_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP-style spawn(agent) must bootstrap session before acquiring turn lock."""
    walker = InteractWalker(agent_id="n.Agent.http")

    class _FakeAgent:
        __entity_name__ = "Agent"
        id = "n.Agent.http"

    locked = False

    async def _bootstrap(agent: Any, *, through: str = "full") -> str:
        if through == "session":
            walker.conversation = SimpleNamespace(id="n.Conversation.http")
            return "ready"
        assert locked, "Interaction creation must hold the full turn lock"
        walker.interaction = SimpleNamespace(id="n.Interaction.http")
        walker.response_bus = SimpleNamespace(finalize_interaction=AsyncMock())
        return "ok"

    monkeypatch.setattr(walker, "_bootstrap_interaction", _bootstrap)
    walker.run = AsyncMock()
    walker._execute_exit_hooks = AsyncMock()
    walker._finalize = AsyncMock()

    lock_entered = []

    class _Lock:
        async def __aenter__(self) -> None:
            nonlocal locked
            locked = True
            lock_entered.append(True)
            assert walker.conversation is not None
            assert walker.conversation.id == "n.Conversation.http"

        async def __aexit__(self, *args: Any) -> bool:
            return False

    monkeypatch.setattr(
        "jvagent.memory.distributed_conversation_lock.conversation_mutation_lock",
        lambda _cid: _Lock(),
    )
    monkeypatch.setattr(
        "jvspatial.core.events.event_bus.register_entity",
        AsyncMock(),
    )

    await walker.spawn(_FakeAgent())

    assert lock_entered
    walker._finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_spawn_does_not_append_during_preceding_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admitted second turn must not become the first turn's predecessor."""

    class Agent:
        __entity_name__ = "Agent"
        id = "n.Agent.concurrent"

    first_running = asyncio.Event()
    second_resolved = asyncio.Event()
    release_first = asyncio.Event()
    events: list[str] = []
    walkers = []
    for name in ("first", "second"):
        walker = InteractWalker(agent_id=Agent.id)

        async def bootstrap(
            agent: Any,
            *,
            through: str = "full",
            w: InteractWalker = walker,
            label: str = name,
        ) -> str:
            if through == "session":
                w.conversation = SimpleNamespace(id="n.Conversation.concurrent")
                if label == "second":
                    second_resolved.set()
                return "ready"
            events.append(f"{label}:create")
            w.interaction = SimpleNamespace(id=f"n.Interaction.{label}")
            return "ok"

        async def run(label: str = name) -> None:
            events.append(f"{label}:run")
            if label == "first":
                first_running.set()
                await release_first.wait()

        async def finalize(label: str = name) -> None:
            events.append(f"{label}:finalize")

        monkeypatch.setattr(walker, "_bootstrap_interaction", bootstrap)
        walker.run = AsyncMock(side_effect=run)
        walker._execute_exit_hooks = AsyncMock()
        walker._finalize = AsyncMock(side_effect=finalize)
        walkers.append(walker)
    monkeypatch.setattr("jvspatial.core.events.event_bus.register_entity", AsyncMock())
    first = asyncio.create_task(walkers[0].spawn(Agent()))
    second = None
    try:
        await asyncio.wait_for(first_running.wait(), 2)
        second = asyncio.create_task(walkers[1].spawn(Agent()))
        await asyncio.wait_for(second_resolved.wait(), 2)
        await asyncio.sleep(0)
        assert events == ["first:create", "first:run"]
    finally:
        release_first.set()
        await asyncio.wait_for(first, 2)
        if second is not None:
            await asyncio.wait_for(second, 2)
    assert events == [
        "first:create",
        "first:run",
        "first:finalize",
        "second:create",
        "second:run",
        "second:finalize",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["access_denied", "no_memory", "session_resolution_error"]
)
async def test_failed_session_resolution_never_creates_or_executes(
    monkeypatch, outcome
):
    class Agent:
        __entity_name__ = "Agent"
        id = "n.Agent.denied"

    walker = InteractWalker(agent_id=Agent.id)
    bootstrap = AsyncMock(return_value=outcome)
    monkeypatch.setattr(walker, "_bootstrap_interaction", bootstrap)
    walker.run = AsyncMock()
    walker._finalize = AsyncMock()
    agent = Agent()
    await walker.spawn(agent)
    bootstrap.assert_awaited_once_with(agent, through="session")
    walker.run.assert_not_awaited()
    walker._finalize.assert_not_awaited()
    assert walker._bootstrap_error == outcome
