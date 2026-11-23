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


async def test_agent_get_response_bus_uses_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Agent, "get_actions", AsyncMock(return_value=[]))
    agent = Agent(
        id="agent-xyz",
        namespace="test",
        name="registry",
        alias="Registry",
        description="",
    )

    bus1 = await agent.get_response_bus()
    # Simulate rematerialize: fresh instance, same id
    agent2 = Agent(
        id="agent-xyz",
        namespace="test",
        name="registry",
        alias="Registry",
        description="",
    )
    bus2 = await agent2.get_response_bus()
    assert bus1 is bus2
    assert bus1 is not None


async def test_existing_bus_refreshes_native_owner_from_enabled_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    actions = AsyncMock(return_value=[NativeHarnessAction()])
    monkeypatch.setattr(Agent, "get_actions", actions)
    agent = Agent(
        id="agent-owner-change",
        namespace="test",
        name="owner",
        alias="Owner",
        description="",
    )
    bus = await agent.get_response_bus()
    assert bus.native_execution is True
    actions.return_value = []
    assert await agent.get_response_bus() is bus
    assert bus.native_execution is False
