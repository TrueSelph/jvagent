"""Tests for core agent-list endpoint pagination behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core import endpoints


def _fake_agent(name: str, alias: str = "", description: str = ""):
    agent = SimpleNamespace(name=name, alias=alias, description=description)
    agent.export = AsyncMock(
        return_value={"name": name, "alias": alias, "description": description}
    )
    return agent


@pytest.mark.asyncio
async def test_list_agents_search_applies_before_pagination_metadata():
    agents = [
        _fake_agent("alpha"),
        _fake_agent("beta", description="target item one"),
        _fake_agent("gamma", alias="target alias"),
        _fake_agent("delta"),
    ]
    with patch.object(endpoints.Agent, "find", AsyncMock(return_value=agents)):
        result = await endpoints.list_agents(page=1, per_page=1, search="target")

    assert result["total"] == 2
    assert result["total_pages"] == 2
    assert result["has_next"] is True
    assert len(result["agents"]) == 1
    assert result["agents"][0]["name"] in {"beta", "gamma"}
