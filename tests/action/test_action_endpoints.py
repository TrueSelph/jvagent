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
