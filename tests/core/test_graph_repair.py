"""Tests for agent graph repair utility."""

from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.core import Edge, Root, get_default_context

from jvagent.core.agent_loader import AgentLoader
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader
from jvagent.core.graph_repair import repair_agent_graph


def _dead_edge_data(edge_id: str, source: str, target: str) -> dict:
    """Build edge data in persistence format (includes context for deserialization)."""
    return {
        "id": edge_id,
        "entity": "Edge",
        "type_code": "e",
        "context": {},
        "source": source,
        "target": target,
        "bidirectional": True,
    }


class TestGraphRepair:
    """Test graph repair functionality."""

    @pytest.mark.asyncio
    async def test_repair_returns_expected_structure(self, temp_dir, test_db):
        """Repair returns dict with all expected keys including memory repair fields."""
        await Root.get()

        result = await repair_agent_graph(dry_run=False)

        assert "memory_repair_agents" in result
        assert "orphaned_interactions_deleted" in result
        assert "orphaned_users_reconnected" in result
        assert "dual_edges_removed" in result
        assert "conversation_first_edges_restored" in result
        assert "conversation_branch_edges_removed" in result
        assert "dead_edges_removed" in result
        assert "orphaned_nodes_reattached" in result
        assert "orphaned_nodes_deleted" in result
        assert "node_edge_ids_synced" in result
        assert "duplicate_edges_removed" in result
        assert "interactions_pruned" in result
        assert "message" in result

    @pytest.mark.asyncio
    async def test_repair_on_clean_installed_agent(self, temp_dir, test_db):
        """Repair on clean graph eventually reports no repairs needed."""
        agent_dir = temp_dir / "agents" / "ns" / "agent1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            """agent: ns/agent1
version: 1.0.0
author: Test
"""
        )

        loader = AgentLoader(str(temp_dir))
        await loader.install_agent("ns", "agent1")

        # First run may fix orphans from install; second run should be clean
        await repair_agent_graph(dry_run=False)
        result = await repair_agent_graph(dry_run=False)

        assert result["message"] == "No repairs needed"
        assert result["dead_edges_removed"] == 0
        assert result["orphaned_nodes_reattached"] == 0
        assert result["orphaned_nodes_deleted"] == 0

    @pytest.mark.asyncio
    async def test_repair_dead_edge_removal(self, temp_dir, test_db):
        """Repair removes edges whose source or target nodes do not exist."""
        await Root.get()

        ctx = get_default_context()
        dead_edge = _dead_edge_data(
            "e.Edge.dead_edge_test",
            "n.Node.nonexistent_source",
            "n.Node.nonexistent_target",
        )
        await ctx.database.save("edge", dead_edge)

        result = await repair_agent_graph(dry_run=False)

        assert result["dead_edges_removed"] == 1
        assert "dead edge(s) removed" in result["message"]

        retrieved = await ctx.database.get("edge", dead_edge["id"])
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_repair_dry_run_no_changes(self, temp_dir, test_db):
        """Dry run reports issues but does not modify the graph."""
        await Root.get()

        ctx = get_default_context()
        dead_edge = _dead_edge_data(
            "e.Edge.dry_run_dead",
            "n.Node.fake_src",
            "n.Node.fake_tgt",
        )
        await ctx.database.save("edge", dead_edge)

        result = await repair_agent_graph(dry_run=True)

        assert result["dry_run"] is True
        assert result["dead_edges_removed"] == 1
        assert "[DRY RUN]" in result["message"]

        retrieved = await ctx.database.get("edge", dead_edge["id"])
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_repair_dry_run_skips_memory_repair(self, temp_dir, test_db):
        """Dry run does not invoke memory repair."""
        await Root.get()

        with patch(
            "jvagent.core.graph_repair._run_memory_repair_all_agents",
            new_callable=AsyncMock,
        ) as mock_memory_repair, patch(
            "jvagent.core.graph_repair._run_interaction_pruning_all_agents",
            new_callable=AsyncMock,
        ) as mock_prune:
            result = await repair_agent_graph(dry_run=True)

        mock_memory_repair.assert_not_called()
        mock_prune.assert_not_called()
        assert result["dry_run"] is True
        assert result["memory_repair_agents"] == 0
        assert result["interactions_pruned"] == 0

    @pytest.mark.asyncio
    async def test_repair_orphan_reattachment(self, temp_dir, test_db):
        """Repair reattaches orphan App node to Root."""
        # Bootstrap app so Root -> App exists
        (temp_dir / "app.yaml").write_text(
            """app: test_app
version: 1.0.0
author: Test
agents: []
"""
        )
        app_loader = AppLoader(str(temp_dir))
        await app_loader.bootstrap_application(update_mode="source")

        app = await App.get()
        assert app is not None

        ctx = get_default_context()
        root = await Root.get()
        edges_data = await ctx.database.find(
            "edge", {"source": root.id, "target": app.id}
        )
        for e in edges_data:
            edge_obj = await ctx._deserialize_entity(Edge, e)
            if edge_obj:
                await ctx.delete(edge_obj, cascade=False)
                break

        result = await repair_agent_graph(dry_run=False)

        assert result["orphaned_nodes_reattached"] >= 1
        assert "orphan(s) reattached" in result["message"]

    @pytest.mark.asyncio
    async def test_memory_repair_runs_before_graph_repair(self, temp_dir, test_db):
        """Memory repair (all agents) executes before graph repair steps."""
        await Root.get()

        call_order = []

        async def fake_memory_repair(recent_minutes):
            call_order.append("memory")
            return {
                "memory_repair_agents": 0,
                "orphaned_interactions_deleted": 0,
                "orphaned_users_reconnected": 0,
                "dual_edges_removed": 0,
                "conversation_first_edges_restored": 0,
                "counters_fixed": 0,
            }

        async def fake_interaction_pruning():
            call_order.append("prune")
            return {"interactions_pruned": 0}

        original_remove_dead_edges = __import__(
            "jvagent.core.graph_repair", fromlist=["_remove_dead_edges"]
        )._remove_dead_edges

        async def patched_remove_dead_edges(context, dry_run):
            call_order.append("graph")
            return await original_remove_dead_edges(context, dry_run)

        with patch(
            "jvagent.core.graph_repair._run_memory_repair_all_agents",
            side_effect=fake_memory_repair,
        ), patch(
            "jvagent.core.graph_repair._remove_dead_edges",
            side_effect=patched_remove_dead_edges,
        ), patch(
            "jvagent.core.graph_repair._run_interaction_pruning_all_agents",
            side_effect=fake_interaction_pruning,
        ):
            await repair_agent_graph(dry_run=False)

        assert call_order[0] == "memory", "Memory repair must run before graph repair"
        assert "graph" in call_order, "Graph repair steps must run"
        assert call_order.index("memory") < call_order.index("graph")
        assert call_order.index("graph") < call_order.index(
            "prune"
        ), "Interaction pruning must run after structural graph repair"
