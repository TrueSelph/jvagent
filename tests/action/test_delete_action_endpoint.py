"""DELETE /actions/{action_id} must not mask a failed deregistration.

The endpoint used to fall through to a direct ``Action.delete(cascade=True)``
whenever ``deregister_action`` returned falsy. That reported 200 while endpoint
unregistration, module unload and ``on_deregister`` had been skipped — the node
vanished but its routes and modules stayed live. Locally ``deregister_action``
effectively always succeeds, so the fall-through only showed up against a
distributed cache/lock backend (Lambda), where post-delete bookkeeping can raise.
"""

from __future__ import annotations

import pytest
from jvspatial.api.exceptions import JVSpatialAPIException, ResourceNotFoundError

from jvagent.action.actions import Actions
from jvagent.action.base import Action
from jvagent.action.endpoints import delete_action
from jvagent.core.agent import Agent

pytestmark = pytest.mark.asyncio

IDENTITY = ("jvagent", "deletable_action")


async def _setup() -> tuple[Agent, Actions]:
    agent = await Agent.create(
        name="delete_agent",
        namespace="test",
        alias="Delete",
        description="d",
    )
    manager = await Actions.create()
    await agent.connect(manager, direction="both")
    return agent, manager


async def _persist_action(agent_id: str, manager: Actions | None) -> Action:
    action = await Action.create(
        namespace=IDENTITY[0],
        label=IDENTITY[1],
        metadata={"class": "DeletableAction", "config": {}},
    )
    object.__setattr__(action, "agent_id", agent_id)
    await action.save()
    if manager is not None:
        await manager.connect(action, direction="both")
    return action


async def test_missing_action_raises_not_found(test_db):
    with pytest.raises(ResourceNotFoundError):
        await delete_action("o.Action.does-not-exist")


async def test_failed_deregister_surfaces_error_and_keeps_node(test_db, monkeypatch):
    """The regression: failure must not be laundered into a 200 + hard delete."""
    agent, manager = await _setup()
    action = await _persist_action(agent.id, manager)

    async def failing_deregister(self, action_id: str) -> bool:
        return False

    monkeypatch.setattr(Actions, "deregister_action", failing_deregister)

    with pytest.raises(JVSpatialAPIException) as excinfo:
        await delete_action(action.id)

    assert action.id in str(excinfo.value)
    assert excinfo.value.details["action_id"] == action.id
    # Node must survive: a hard delete here would skip lifecycle cleanup.
    assert await Action.get(action.id) is not None


async def test_deregister_false_after_node_gone_is_idempotent_success(
    test_db, monkeypatch
):
    """Delete landed but later bookkeeping raised — still a successful delete."""
    agent, manager = await _setup()
    action = await _persist_action(agent.id, manager)

    async def deletes_then_reports_failure(self, action_id: str) -> bool:
        node = await Action.get(action_id)
        if node:
            await node.delete(cascade=True)
        return False

    monkeypatch.setattr(Actions, "deregister_action", deletes_then_reports_failure)

    result = await delete_action(action.id)

    assert result == {"message": "Action deleted successfully"}
    assert await Action.get(action.id) is None


async def test_orphaned_action_without_agent_is_deleted_directly(test_db):
    """No manager to route through — direct cascade delete is the only option."""
    action = await _persist_action("o.Agent.missing", None)

    result = await delete_action(action.id)

    assert result == {"message": "Action deleted successfully"}
    assert await Action.get(action.id) is None
