"""Live DB integration smoke for stress-seed (N=1, M=1)."""

from __future__ import annotations

import pytest

from jvagent.core.agent_loader import AgentLoader
from jvagent.core.app_loader import AppLoader
from jvagent.stress_seed_graph import StressSeedConfig, execute_stress_seed_graph


@pytest.mark.asyncio
async def test_stress_seed_integration_n1_m1(temp_dir, test_db) -> None:
    (temp_dir / "app.yaml").write_text(
        """app: stress_seed_test
version: 1.0.0
author: Test
agents: []
"""
    )
    agent_dir = temp_dir / "agents" / "ns" / "seed_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        """agent: ns/seed_agent
version: 1.0.0
author: Test
"""
    )

    app_loader = AppLoader(str(temp_dir))
    await app_loader.bootstrap_application(update_mode="source")

    loader = AgentLoader(str(temp_dir))
    await loader.install_agent("ns", "seed_agent")

    cfg = StressSeedConfig(
        user_memory_nodes=1,
        interactions_per_user_memory_node=1,
    )
    summary = await execute_stress_seed_graph(cfg)

    assert summary["ok"] is True
    assert summary["user_memory_nodes"] == 1
    assert summary["total_interactions"] == 1
    assert summary["agent"] == "ns/seed_agent"
