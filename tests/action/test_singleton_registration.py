"""Singleton action registration must never leave multiple nodes per archetype."""

from __future__ import annotations

import pytest

from jvagent.action.actions import Actions
from jvagent.action.base import Action
from jvagent.action.identity import find_records_by_archetype
from jvagent.core.agent import Agent

pytestmark = pytest.mark.asyncio

ARCHETYPE = "AccessControlAction"
IDENTITY = ("jvagent", "access_control_action")


def _singleton_metadata(*, singleton: bool = True) -> dict:
    return {
        "class": ARCHETYPE,
        "config": {"singleton": singleton},
    }


async def _setup():
    agent = await Agent.create(
        name="singleton_agent",
        namespace="test",
        alias="Singleton",
        description="d",
    )
    manager = await Actions.create()
    await agent.connect(manager, direction="both")
    return agent, manager


async def _persist_action(
    agent_id: str,
    manager: Actions,
    *,
    connect: bool = True,
    metadata: dict | None = None,
) -> Action:
    action = await Action.create(
        namespace=IDENTITY[0],
        label=IDENTITY[1],
        metadata=metadata or _singleton_metadata(),
    )
    object.__setattr__(action, "agent_id", agent_id)
    await action.save()
    if connect:
        await manager.connect(action, direction="both")
    return action


async def _fresh_register_instance(agent_id: str) -> Action:
    action = await Action.create(
        namespace=IDENTITY[0],
        label=IDENTITY[1],
        metadata=_singleton_metadata(),
    )
    object.__setattr__(action, "agent_id", agent_id)
    return action


async def _count_archetype(agent_id: str) -> int:
    return len(await find_records_by_archetype(agent_id, ARCHETYPE))


async def test_register_collapses_existing_duplicates(test_db):
    agent, manager = await _setup()
    await _persist_action(agent.id, manager, connect=True)
    await _persist_action(agent.id, manager, connect=False)
    await _persist_action(agent.id, manager, connect=False)

    assert await _count_archetype(agent.id) == 3

    ok = await manager.register_action(await _fresh_register_instance(agent.id))

    assert ok is True
    assert await _count_archetype(agent.id) == 1
    survivors = await Action.find(
        {
            "context.agent_id": agent.id,
            "context.namespace": IDENTITY[0],
            "context.label": IDENTITY[1],
        }
    )
    assert len(survivors) == 1
    assert await manager.is_connected_to(survivors[0])


async def test_register_rejects_second_singleton_with_different_label(test_db):
    agent, manager = await _setup()
    first = await Action.create(
        namespace="jvagent",
        label="access_control_action",
        metadata=_singleton_metadata(),
    )
    object.__setattr__(first, "agent_id", agent.id)
    await first.save()
    await manager.connect(first, direction="both")

    second = await Action.create(
        namespace="contrib",
        label="other_access_control",
        metadata={"class": ARCHETYPE, "config": {"singleton": True}},
    )
    object.__setattr__(second, "agent_id", agent.id)

    ok = await manager.register_action(second)

    assert ok is False
    assert await _count_archetype(agent.id) == 1


async def test_register_second_call_reuses_without_creating(test_db):
    agent, manager = await _setup()
    first = await _persist_action(agent.id, manager)

    ok = await manager.register_action(await _fresh_register_instance(agent.id))

    assert ok is True
    assert await _count_archetype(agent.id) == 1
    survivors = await Action.find(
        {
            "context.agent_id": agent.id,
            "context.namespace": IDENTITY[0],
            "context.label": IDENTITY[1],
        }
    )
    assert survivors[0].id == first.id


async def test_non_singleton_allows_multiple_same_archetype_label(test_db):
    agent, manager = await _setup()
    meta = {"class": "CustomAction", "config": {"singleton": False}}

    first = await Action.create(namespace="a", label="custom", metadata=meta)
    object.__setattr__(first, "agent_id", agent.id)
    await first.save()

    second = await Action.create(namespace="b", label="custom", metadata=meta)
    object.__setattr__(second, "agent_id", agent.id)

    assert await manager.register_action(first) is True
    assert await manager.register_action(second) is True

    found = await Action.find({"context.agent_id": agent.id})
    labels = {(a.namespace, a.label) for a in found}
    assert ("a", "custom") in labels
    assert ("b", "custom") in labels


async def test_reconcile_race_loser_removes_duplicate(test_db):
    from jvagent.action.registration import reconcile_singleton_after_create

    agent, manager = await _setup()
    first = await _persist_action(agent.id, manager, connect=True)
    second = await _persist_action(agent.id, manager, connect=True)
    loser, keeper = (first, second) if first.id > second.id else (second, first)
    assert await _count_archetype(agent.id) == 2

    survived = await reconcile_singleton_after_create(
        loser,
        manager,
        archetype=ARCHETYPE,
        action_existed_before=False,
    )

    assert survived is False
    assert await Action.get(loser.id) is None
    assert await Action.get(keeper.id) is not None
    assert await _count_archetype(agent.id) == 1


async def test_register_actions_skips_post_register_for_race_loser(
    test_db, monkeypatch
):
    calls: list[str] = []
    original_post_register = Action.post_register

    async def track_post_register(self) -> None:
        calls.append(self.id)
        await original_post_register(self)

    monkeypatch.setattr(Action, "post_register", track_post_register)

    async def fake_reconcile(action, actions_manager, **kwargs):
        if await actions_manager.is_connected_to(action):
            await actions_manager.disconnect(action)
        await action.delete(cascade=True)
        return False

    monkeypatch.setattr(
        "jvagent.action.registration.reconcile_singleton_after_create",
        fake_reconcile,
    )

    agent, manager = await _setup()
    action = await _fresh_register_instance(agent.id)

    await manager.register_actions([action])

    assert await Action.get(action.id) is None
    assert action.id not in calls


async def test_get_access_control_action_heals_duplicates(test_db):
    agent, manager = await _setup()
    await _persist_action(agent.id, manager)
    await _persist_action(agent.id, manager, connect=False)
    assert await _count_archetype(agent.id) == 2

    action = await agent.get_access_control_action()

    assert action is not None
    assert await _count_archetype(agent.id) == 1


async def test_dedupe_singleton_actions_by_archetype(test_db):
    from jvagent.core.agent_loader import AgentLoader

    agent, manager = await _setup()
    await _persist_action(agent.id, manager)
    await _persist_action(agent.id, manager, connect=False)
    assert await _count_archetype(agent.id) == 2

    loader = AgentLoader()
    removed = await loader._dedupe_singleton_actions_by_archetype(agent, manager)

    assert removed >= 1
    assert await _count_archetype(agent.id) == 1


async def test_delete_action_routes_through_deregister(test_db, monkeypatch):
    from jvagent.action.endpoints import delete_action

    agent, manager = await _setup()
    action = await _persist_action(agent.id, manager)
    called: list[str] = []
    original = Actions.deregister_action

    async def spy_deregister(self, action_id: str) -> bool:
        called.append(action_id)
        return await original(self, action_id)

    monkeypatch.setattr(Actions, "deregister_action", spy_deregister)

    result = await delete_action(action.id)

    assert result == {"message": "Action deleted successfully"}
    assert called == [action.id]
    assert await Action.get(action.id) is None
