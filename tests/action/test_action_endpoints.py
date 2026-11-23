"""Tests for generic action REST helpers in jvagent.action.endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ResourceNotFoundError


@pytest.mark.asyncio
async def test_get_agent_action_by_entity_agent_not_found():
    from jvagent.action import endpoints as ep

    with patch.object(ep.Agent, "get", new_callable=AsyncMock, return_value=None):
        with pytest.raises(ResourceNotFoundError, match="Agent with ID"):
            await ep.get_agent_action_by_entity("missing-agent", "AccessControlAction")


@pytest.mark.asyncio
async def test_get_agent_action_by_entity_no_matching_action():
    from jvagent.action import endpoints as ep

    agent = MagicMock()
    agent.get_action_by_type = AsyncMock(return_value=None)
    with patch.object(ep.Agent, "get", new_callable=AsyncMock, return_value=agent):
        with pytest.raises(ResourceNotFoundError, match="No action with entity"):
            await ep.get_agent_action_by_entity("agent-1", "FooAction")


@pytest.mark.asyncio
async def test_get_agent_action_by_entity_success():
    from jvagent.action import endpoints as ep

    action = MagicMock()
    action.export = AsyncMock(return_value={"id": "n.Foo.1", "entity": "FooAction"})
    agent = MagicMock()
    agent.get_action_by_type = AsyncMock(return_value=action)
    with patch.object(ep.Agent, "get", new_callable=AsyncMock, return_value=agent):
        out = await ep.get_agent_action_by_entity("agent-1", "FooAction")
    assert out["action"]["id"] == "n.Foo.1"
    assert out["action"]["entity"] == "FooAction"
    agent.get_action_by_type.assert_awaited_once_with("FooAction")


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_fails", [False, True])
async def test_update_policy_invalidates_warm_action_instances(
    reload_fails: bool,
) -> None:
    from jvspatial.core.annotations import attribute

    from jvagent.action import endpoints as ep
    from jvagent.action.base import Action
    from jvagent.core.cache import (
        cache_actions,
        get_cached_actions,
        invalidate_action_cache,
    )

    class RuntimePolicyAction(Action):
        limit: str = attribute(default="20.00")

        async def on_reload(self) -> None:
            if reload_fails:
                raise RuntimeError("Reload unavailable")

    action = await RuntimePolicyAction.create(agent_id="policy-cache-agent")
    stale = action.model_copy()
    await cache_actions(action.agent_id, [stale], enabled_only=True)
    assert (await get_cached_actions(action.agent_id, True))[0].limit == "20.00"
    try:
        if reload_fails:
            with pytest.raises(RuntimeError, match="Reload unavailable"):
                await ep.update_action(action.id, properties={"limit": "0.40"})
        else:
            result = await ep.update_action(action.id, properties={"limit": "0.40"})
            assert result["update_result"]["updated"]["limit"] == "0.40"
        assert await get_cached_actions(action.agent_id, True) is None
        fresh = await RuntimePolicyAction.get(action.id)
        assert fresh.limit == "0.40"
    finally:
        await invalidate_action_cache(action.agent_id)
