"""Boot-time action dedupe converges duplicate singleton nodes (AUDIT-core C1).

Canonical action identity is (agent_id, namespace, label). On the default JSON
adapter uniqueness is not enforced, so prior races / partial installs / concurrent
worker boots can leave several nodes with the same identity. AgentLoader
._dedupe_actions_by_identity must collapse each identity to a single node on
every install."""

from __future__ import annotations

import pytest
from jvspatial.core.entities.node import Node

from jvagent.action.actions import Actions
from jvagent.action.base import Action
from jvagent.core.agent import Agent
from jvagent.core.agent_loader import AgentLoader

pytestmark = pytest.mark.asyncio


async def _make_action(agent_id, ns, label, manager, *, enabled=True, connect=True):
    action = await Action.create(namespace=ns, label=label, enabled=enabled)
    # agent_id is protected; set explicitly then persist.
    object.__setattr__(action, "agent_id", agent_id)
    await action.save()
    if connect:
        await manager.connect(action, direction="both")
    return action


async def _setup(tmp_path):
    agent = await Agent.create(
        name="dedupe_agent", namespace="test", alias="Dedupe", description="d"
    )
    manager = await Actions.create()
    await agent.connect(manager, direction="both")
    loader = AgentLoader(str(tmp_path))
    return agent, manager, loader


async def _find_actions(agent_id, ns, label):
    return await Action.find(
        {
            "context.agent_id": agent_id,
            "context.namespace": ns,
            "context.label": label,
        }
    )


async def test_dedupe_collapses_triplicate(test_db, tmp_path):
    agent, manager, loader = await _setup(tmp_path)
    for _ in range(3):
        await _make_action(agent.id, "jvagent", "reply", manager)
    await _make_action(agent.id, "jvagent", "orchestrator", manager)

    removed = await loader._dedupe_actions_by_identity(agent, manager)

    assert removed == 2
    replies = await _find_actions(agent.id, "jvagent", "reply")
    assert len(replies) == 1
    assert await manager.is_connected_to(replies[0])
    # Distinct identity untouched.
    orch = await _find_actions(agent.id, "jvagent", "orchestrator")
    assert len(orch) == 1


async def test_dedupe_noop_when_unique(test_db, tmp_path):
    agent, manager, loader = await _setup(tmp_path)
    await _make_action(agent.id, "jvagent", "reply", manager)
    await _make_action(agent.id, "jvagent", "orchestrator", manager)

    removed = await loader._dedupe_actions_by_identity(agent, manager)

    assert removed == 0
    assert len(await _find_actions(agent.id, "jvagent", "reply")) == 1
    assert len(await _find_actions(agent.id, "jvagent", "orchestrator")) == 1


async def test_dedupe_prefers_connected_survivor(test_db, tmp_path):
    agent, manager, loader = await _setup(tmp_path)
    # Unconnected duplicate created first (smaller id), connected one second.
    unconnected = await _make_action(
        agent.id, "jvagent", "reply", manager, connect=False
    )
    connected = await _make_action(agent.id, "jvagent", "reply", manager, connect=True)

    removed = await loader._dedupe_actions_by_identity(agent, manager)

    assert removed == 1
    survivors = await _find_actions(agent.id, "jvagent", "reply")
    assert len(survivors) == 1
    # The connected node must be the survivor even though it has the larger id.
    assert survivors[0].id == connected.id
    assert await manager.is_connected_to(survivors[0])
    # The removed node is gone from the graph.
    assert await Node.get(unconnected.id) is None


async def test_dedupe_reconnects_survivor_when_kept_node_was_unconnected(
    test_db, tmp_path
):
    """If every duplicate for an identity is unconnected, the survivor is wired
    back to the manager so the walker can see it."""
    agent, manager, loader = await _setup(tmp_path)
    await _make_action(agent.id, "jvagent", "reply", manager, connect=False)
    await _make_action(agent.id, "jvagent", "reply", manager, connect=False)

    removed = await loader._dedupe_actions_by_identity(agent, manager)

    assert removed == 1
    survivors = await _find_actions(agent.id, "jvagent", "reply")
    assert len(survivors) == 1
    assert await manager.is_connected_to(survivors[0])


async def test_dedupe_isolated_per_agent(test_db, tmp_path):
    """Duplicates for one agent must not touch another agent's identical labels."""
    agent_a, manager_a, loader = await _setup(tmp_path)
    agent_b = await Agent.create(
        name="other", namespace="test", alias="Other", description="d"
    )
    manager_b = await Actions.create()
    await agent_b.connect(manager_b, direction="both")

    await _make_action(agent_a.id, "jvagent", "reply", manager_a)
    await _make_action(agent_a.id, "jvagent", "reply", manager_a)
    await _make_action(agent_b.id, "jvagent", "reply", manager_b)

    removed = await loader._dedupe_actions_by_identity(agent_a, manager_a)

    assert removed == 1
    assert len(await _find_actions(agent_a.id, "jvagent", "reply")) == 1
    # Agent B's action is untouched.
    assert len(await _find_actions(agent_b.id, "jvagent", "reply")) == 1
