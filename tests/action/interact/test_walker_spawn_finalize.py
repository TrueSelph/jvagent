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
