"""Process-level ResponseBus registry survives Agent rematerialize."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.response.response_bus import (
    clear_agent_response_bus,
    get_agent_response_bus,
)
from jvagent.core.agent import Agent

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_agent_response_bus()
    yield
    clear_agent_response_bus()


async def test_same_agent_id_returns_same_bus():
    a = await get_agent_response_bus("agent-1")
    b = await get_agent_response_bus("agent-1")
    assert a is b


async def test_agent_get_response_bus_uses_registry(monkeypatch):
    agent = Agent.__new__(Agent)
    object.__setattr__(agent, "id", "agent-xyz")
    object.__setattr__(agent, "_response_bus", None)

    bus1 = await agent.get_response_bus()
    # Simulate rematerialize: fresh instance, same id
    agent2 = Agent.__new__(Agent)
    object.__setattr__(agent2, "id", "agent-xyz")
    object.__setattr__(agent2, "_response_bus", None)
    bus2 = await agent2.get_response_bus()
    assert bus1 is bus2
