"""InteractWalker spawn/finalize semantics (C2-related)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interact.interact_walker import InteractWalker


@pytest.mark.asyncio
async def test_spawn_finalizes_after_queue_drain(monkeypatch):
    walker = InteractWalker(agent_id="n.Agent.spawn")
    walker.conversation = SimpleNamespace(id="n.Conversation.c1")
    walker.interaction = SimpleNamespace(id="n.Interaction.i1")
    walker.response_bus = SimpleNamespace(finalize_interaction=AsyncMock())
    walker.queue._backing = []
    walker.queue.append = AsyncMock()

    run_order = []

    async def _run():
        run_order.append("run")

    async def _finalize():
        run_order.append("finalize")

    walker.run = AsyncMock(side_effect=_run)
    walker._execute_exit_hooks = AsyncMock()
    walker._finalize = AsyncMock(side_effect=_finalize)

    lock_entered = []

    class _Lock:
        async def __aenter__(self):
            lock_entered.append(True)

        async def __aexit__(self, *args):
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
async def test_spawn_bootstraps_before_conversation_lock(monkeypatch):
    """HTTP-style spawn(agent) must bootstrap session before acquiring turn lock."""
    walker = InteractWalker(agent_id="n.Agent.http")

    class _FakeAgent:
        __entity_name__ = "Agent"
        id = "n.Agent.http"

    async def _bootstrap(agent, *, through="full"):
        if through == "session":
            walker.conversation = SimpleNamespace(id="n.Conversation.http")
            return "ready"
        walker.interaction = SimpleNamespace(id="n.Interaction.http")
        walker.response_bus = SimpleNamespace(finalize_interaction=AsyncMock())
        return "ok"

    monkeypatch.setattr(walker, "_bootstrap_interaction", _bootstrap)
    walker.run = AsyncMock()
    walker._execute_exit_hooks = AsyncMock()
    walker._finalize = AsyncMock()

    lock_entered = []

    class _Lock:
        async def __aenter__(self):
            lock_entered.append(True)
            assert walker.conversation is not None
            assert walker.conversation.id == "n.Conversation.http"

        async def __aexit__(self, *args):
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
