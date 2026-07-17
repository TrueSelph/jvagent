"""_recount_agent_statistics must count only this app's agents (AUDIT-core M21).

Agent.find({}) is global — in a shared/embedded DB it counts other apps' agents.
The recount must scope to the agents connected to this app's Agents manager."""

from __future__ import annotations

import pytest

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader

pytestmark = pytest.mark.asyncio


async def _agent(name, enabled):
    return await Agent.create(
        name=name, namespace="t", alias=name.title(), description="d", enabled=enabled
    )


async def test_recount_scopes_to_connected_agents(test_db):
    app = await App.create(app_id="app_a")
    mgr = await Agents.create(total_agents=0, active_agents=0)
    await app.connect(mgr)

    a1 = await _agent("a1", True)
    a2 = await _agent("a2", False)
    await mgr.connect(a1, direction="both")
    await mgr.connect(a2, direction="both")

    # A third agent NOT connected to this app's manager (e.g. another app in a
    # shared DB). It must not be counted.
    await _agent("other", True)

    loader = AppLoader(".")
    await loader._recount_agent_statistics(app)

    refreshed = await app.node(node="Agents")
    assert refreshed.total_agents == 2  # not 3
    assert refreshed.active_agents == 1  # a1 only
