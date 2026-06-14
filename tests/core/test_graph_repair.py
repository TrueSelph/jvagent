"""Tests for agent graph repair utility."""

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.core import Edge, Node, Root, get_default_context

from jvagent.core import graph_repair_job
from jvagent.core.agent_loader import AgentLoader
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader
from jvagent.core.graph_repair import repair_agent_graph
from jvagent.core.repair_state import RepairState
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.manager import Memory
from jvagent.memory.user import User

# Synchronous engine: re-invoke until the pipeline reports completed.
_REPAIR_MAX_STEPS = 20_000


async def _repair_to_completion(**kwargs: Any) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for _ in range(_REPAIR_MAX_STEPS):
        last = await repair_agent_graph(**kwargs)
        if last.get("status") == "completed":
            return last
    raise AssertionError("repair did not complete within %d steps" % _REPAIR_MAX_STEPS)


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

        result = await _repair_to_completion(dry_run=False)

        assert "memory_repair_agents" in result
        assert "orphaned_interactions_deleted" in result
        assert "orphaned_users_reconnected" in result
        assert "dual_edges_removed" in result
        assert "conversation_first_edges_restored" in result
        assert "conversation_branch_edges_removed" in result
        assert "duplicate_apps_removed" in result
        assert "duplicate_agents_removed" in result
        assert "duplicate_actions_managers_removed" in result
        assert "duplicate_memory_nodes_removed" in result
        assert "duplicate_singleton_actions_removed" in result
        assert "dead_edges_removed" in result
        assert "orphaned_nodes_reattached" in result
        assert "orphaned_nodes_deleted" in result
        assert "node_edge_ids_synced" in result
        assert "duplicate_edges_removed" in result
        assert "interactions_pruned" in result
        assert "message" in result
        assert result.get("status") == "completed"
        assert "next_repair_cursor" not in result

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
        await _repair_to_completion(dry_run=False)
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

        result = await _repair_to_completion(dry_run=False)

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

        result = await _repair_to_completion(dry_run=True)

        assert result["dry_run"] is True
        assert result["dead_edges_removed"] == 1
        assert "[DRY RUN]" in result["message"]

        retrieved = await ctx.database.get("edge", dead_edge["id"])
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_repair_dry_run_skips_memory_repair(self, temp_dir, test_db):
        """Dry run does not invoke memory repair or pruning ticks."""
        await Root.get()

        with patch(
            "jvagent.core.graph_repair_job._tick_memory_counters",
            new_callable=AsyncMock,
        ) as mock_memory_counters, patch(
            "jvagent.core.graph_repair_job._tick_memory_agents",
            new_callable=AsyncMock,
        ) as mock_memory_agents, patch(
            "jvagent.core.graph_repair_job._tick_prune_agents",
            new_callable=AsyncMock,
        ) as mock_prune:
            result = await _repair_to_completion(dry_run=True)

        mock_memory_counters.assert_not_called()
        mock_memory_agents.assert_not_called()
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
        # Remove every edge between Root and App (both directions; bidirectional
        # or duplicate rows can leave a connection if we only delete one).
        for q in (
            {"source": root.id, "target": app.id},
            {"source": app.id, "target": root.id},
        ):
            for e in await ctx.database.find("edge", q):
                edge_obj = await ctx._deserialize_entity(Edge, e)
                if edge_obj:
                    await ctx.delete(edge_obj, cascade=False)
        # Reload to avoid cached edges / stale is_connected on node instances
        App.clear_cache()
        app_live = await ctx.get(Node, app.id)
        root_live = await ctx.get(Node, root.id)
        assert app_live and root_live
        assert not await root_live.is_connected_to(
            app_live
        ), "Test setup must leave App disconnected from Root"

        result = await _repair_to_completion(dry_run=False)

        assert result["orphaned_nodes_reattached"] >= 1
        assert "orphan(s) reattached" in result["message"]

    @pytest.mark.asyncio
    async def test_memory_repair_runs_before_graph_repair(self, temp_dir, test_db):
        """Memory repair (all agents) executes before graph repair steps."""
        await Root.get()

        call_order = []

        real_tick_mc = graph_repair_job._tick_memory_counters
        real_tick_dead = graph_repair_job._tick_dead_edges
        real_tick_prune = graph_repair_job._tick_prune_agents

        async def patched_tick_memory_counters(state, limits):
            call_order.append("memory")
            return await real_tick_mc(state, limits)

        async def patched_tick_dead_edges(context, state, limits):
            call_order.append("graph")
            return await real_tick_dead(context, state, limits)

        async def patched_tick_prune(state, limits):
            call_order.append("prune")
            return await real_tick_prune(state, limits)

        with patch(
            "jvagent.core.graph_repair_job._tick_memory_counters",
            side_effect=patched_tick_memory_counters,
        ), patch(
            "jvagent.core.graph_repair_job._tick_dead_edges",
            side_effect=patched_tick_dead_edges,
        ), patch(
            "jvagent.core.graph_repair_job._tick_prune_agents",
            side_effect=patched_tick_prune,
        ):
            await _repair_to_completion(dry_run=False)

        assert call_order[0] == "memory", "Memory repair must run before graph repair"
        assert "graph" in call_order, "Graph repair steps must run"
        assert call_order.index("memory") < call_order.index("graph")
        assert call_order.index("graph") < call_order.index(
            "prune"
        ), "Interaction pruning must run after structural graph repair"

    @pytest.mark.asyncio
    async def test_repair_batched_resumes_until_completed(self, temp_dir, test_db):
        """Small internal batches resume through persisted RepairState until done."""
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

        out: dict = {}
        guard = 0
        while guard < 20_000:
            out = await repair_agent_graph(
                dry_run=False,
                max_seconds=0.5,
                batch_size=1,
            )
            if out["status"] == "completed":
                break
            repair_state = await RepairState.current(app)
            assert repair_state is not None
            guard += 1

        assert out["status"] == "completed"
        assert out["phase"] == graph_repair_job.PH_DONE
        assert await RepairState.current(app) is None

        once = await _repair_to_completion(dry_run=False)
        assert once.get("status") == "completed"
        assert once["dead_edges_removed"] == 0
        assert once["message"] == "No repairs needed"

    @pytest.mark.asyncio
    async def test_repair_state_roundtrip(self, temp_dir, test_db):
        """State serialization and persistence roundtrip preserves repair progress."""
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

        state = graph_repair_job._initial_session_state(False, None)
        state["phase"] = graph_repair_job.PH_DEAD_EDGES
        state["cursor"] = {"last_edge_id": "e.test"}
        state["result"]["dead_edges_removed"] = 3
        payload = graph_repair_job.state_to_dict(state)
        restored = graph_repair_job.state_from_dict(
            payload, dry_run=False, recent_minutes=None
        )
        assert restored["phase"] == graph_repair_job.PH_DEAD_EDGES
        assert restored["cursor"]["last_edge_id"] == "e.test"
        assert restored["result"]["dead_edges_removed"] == 3

        repair_state = await RepairState.begin(
            app,
            dry_run=False,
            recent_minutes=None,
            version=graph_repair_job.STATE_VERSION,
        )
        await repair_state.save_progress(
            phase=payload["phase"],
            cursor=payload["cursor"],
            result=payload["result"],
        )
        loaded = await RepairState.current(app)
        assert loaded is not None
        assert loaded.phase == graph_repair_job.PH_DEAD_EDGES
        assert loaded.cursor.get("last_edge_id") == "e.test"
        assert loaded.result.get("dead_edges_removed") == 3
        await loaded.finish()

    @pytest.mark.asyncio
    async def test_repair_state_resets_on_dry_run_mismatch(self, temp_dir, test_db):
        """Incompatible dry_run call resets persisted RepairState cleanly."""
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

        stale = await RepairState.begin(
            app,
            dry_run=False,
            recent_minutes=None,
            version=graph_repair_job.STATE_VERSION,
        )
        await stale.save_progress(
            phase=graph_repair_job.PH_DEAD_EDGES,
            cursor={"last_edge_id": "e.stale"},
            result=graph_repair_job._new_result_counters(),
        )
        stale_id = stale.id

        out = await _repair_to_completion(
            dry_run=True,
            max_seconds=0.5,
            batch_size=1,
        )

        ctx = get_default_context()
        assert await ctx.database.get("node", stale_id) is None
        if out["status"] == "in_progress":
            current = await RepairState.current(app)
            assert current is not None
            assert current.dry_run is True
        else:
            assert out["status"] == "completed"

    @pytest.mark.asyncio
    async def test_schema_app_dedupe_keeps_densest(self, temp_dir, test_db):
        """App dedupe removes all but the densest app node."""
        state = {
            "phase": graph_repair_job.PH_SCHEMA_APP_DEDUPE,
            "cursor": {},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
        }
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)
        context = SimpleNamespace(
            database=SimpleNamespace(count=AsyncMock(return_value=3))
        )

        app_a = SimpleNamespace(id="n.App.a", delete=AsyncMock())
        app_b = SimpleNamespace(id="n.App.b", delete=AsyncMock())
        app_c = SimpleNamespace(id="n.App.c", delete=AsyncMock())
        density_by_id = {
            app_a.id: {"n.A", "n.B"},
            app_b.id: {"n.C"},
            app_c.id: {"n.D", "n.E", "n.F"},
        }

        async def reachable(_, node):
            return density_by_id[node.id]

        with patch(
            "jvagent.core.app.App.find",
            new=AsyncMock(return_value=[app_a, app_b, app_c]),
        ), patch(
            "jvagent.core.graph_repair._compute_reachable_nodes_excluding_root",
            side_effect=reachable,
        ), patch(
            "jvagent.core.app.App.clear_cache"
        ) as mock_clear_cache:
            out = await graph_repair_job._tick_schema_app_dedupe(context, state, limits)

        assert out is True
        app_c.delete.assert_not_called()
        app_a.delete.assert_awaited_once()
        app_b.delete.assert_awaited_once()
        mock_clear_cache.assert_called_once()
        assert state["result"]["duplicate_apps_removed"] == 2
        assert state["phase"] == graph_repair_job.PH_SCHEMA_AGENT_DEDUPE

    @pytest.mark.asyncio
    async def test_schema_agent_dedupe_keeps_densest(self, temp_dir, test_db):
        """Agent dedupe keeps the densest duplicate and removes the rest."""
        state = {
            "phase": graph_repair_job.PH_SCHEMA_AGENT_DEDUPE,
            "cursor": {},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
        }
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)

        drop = SimpleNamespace(
            id="n.Agent.001", namespace="ns", name="same", delete=AsyncMock()
        )
        keep = SimpleNamespace(
            id="n.Agent.002", namespace="ns", name="same", delete=AsyncMock()
        )
        other = SimpleNamespace(id="n.Agent.003", namespace="ns", name="other")

        async def density(_, agent):
            return {"n.1", "n.2", "n.3"} if agent.id == keep.id else {"n.1"}

        async def get_agent(agent_id):
            return {keep.id: keep, drop.id: drop}.get(agent_id)

        with patch(
            "jvagent.core.graph_repair_job._entity_count", new=AsyncMock(return_value=3)
        ), patch(
            "jvagent.core.agent.Agent.find",
            new=AsyncMock(return_value=[keep, drop, other]),
        ), patch(
            "jvagent.core.agent.Agent.get", side_effect=get_agent
        ), patch(
            "jvagent.core.graph_repair._compute_reachable_nodes_below",
            side_effect=density,
        ):
            out = await graph_repair_job._tick_schema_agent_dedupe(state, limits)

        assert out is True
        drop.delete.assert_awaited_once()
        keep.delete.assert_not_called()
        assert state["result"]["duplicate_agents_removed"] == 1
        assert state["phase"] == graph_repair_job.PH_SCHEMA_ACTIONS_DEDUPE

    @pytest.mark.asyncio
    async def test_schema_actions_and_memory_dedupe(self, temp_dir, test_db):
        """Per-agent Actions and Memory manager duplicates are removed."""
        state = {
            "phase": graph_repair_job.PH_SCHEMA_ACTIONS_DEDUPE,
            "cursor": {"agent_ids": None, "agent_index": 0},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
        }
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)

        actions_keep = SimpleNamespace(id="n.Actions.001")
        actions_drop = SimpleNamespace(id="n.Actions.002", delete=AsyncMock())
        memory_keep = SimpleNamespace(id="n.Memory.001")
        memory_drop = SimpleNamespace(id="n.Memory.002", delete=AsyncMock())

        class FakeAgent:
            def __init__(self):
                self.id = "n.Agent.001"

            async def nodes(self, node):
                if node == "Actions":
                    return [actions_keep, actions_drop]
                if node == "Memory":
                    return [memory_keep, memory_drop]
                return []

        agent = FakeAgent()

        with patch(
            "jvagent.core.agent.Agent.find", new=AsyncMock(return_value=[agent])
        ), patch("jvagent.core.agent.Agent.get", new=AsyncMock(return_value=agent)):
            out_actions = await graph_repair_job._tick_schema_actions_dedupe(
                state, limits
            )
            out_memory = await graph_repair_job._tick_schema_memory_dedupe(
                state, limits
            )

        assert out_actions is True
        assert out_memory is True
        actions_drop.delete.assert_awaited_once()
        memory_drop.delete.assert_awaited_once()
        assert state["result"]["duplicate_actions_managers_removed"] == 1
        assert state["result"]["duplicate_memory_nodes_removed"] == 1
        assert state["phase"] == graph_repair_job.PH_SCHEMA_SINGLETON_ACTIONS

    @pytest.mark.asyncio
    async def test_schema_memory_dedupe_keeps_densest(self, temp_dir, test_db):
        """Memory dedupe keeps the memory with richer descendant graph."""
        state = {
            "phase": graph_repair_job.PH_SCHEMA_MEMORY_DEDUPE,
            "cursor": {"agent_ids": None, "agent_index": 0},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
        }
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)

        keep = SimpleNamespace(id="n.Memory.002")
        drop = SimpleNamespace(id="n.Memory.001", delete=AsyncMock())

        class FakeAgent:
            id = "n.Agent.001"

            async def nodes(self, node):
                if node == "Memory":
                    return [drop, keep]
                return []

        async def density(_, node):
            return {"n.1", "n.2", "n.3"} if node.id == keep.id else {"n.1"}

        agent = FakeAgent()
        with patch(
            "jvagent.core.agent.Agent.find", new=AsyncMock(return_value=[agent])
        ), patch(
            "jvagent.core.agent.Agent.get", new=AsyncMock(return_value=agent)
        ), patch(
            "jvagent.core.graph_repair._compute_reachable_nodes_below",
            side_effect=density,
        ):
            out = await graph_repair_job._tick_schema_memory_dedupe(state, limits)

        assert out is True
        drop.delete.assert_awaited_once()
        assert state["result"]["duplicate_memory_nodes_removed"] == 1
        assert state["phase"] == graph_repair_job.PH_SCHEMA_SINGLETON_ACTIONS

    @pytest.mark.asyncio
    async def test_schema_singleton_action_dedupe_and_dry_run(self, temp_dir, test_db):
        """Singleton action dedupe removes duplicates and dry-run only counts."""
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)
        context = SimpleNamespace(
            database=SimpleNamespace(
                find=AsyncMock(
                    return_value=[
                        {
                            "id": "n.Action.001",
                            "entity": "Action",
                            "context": {
                                "agent_id": "n.Agent.001",
                                "namespace": "jvagent",
                                "label": "intro",
                                "metadata": {
                                    "class": "IntroAction",
                                    "config": {"singleton": True},
                                },
                            },
                        },
                        {
                            "id": "n.Action.002",
                            "entity": "Action",
                            "context": {
                                "agent_id": "n.Agent.001",
                                "namespace": "jvagent",
                                "label": "intro-dup",
                                "metadata": {
                                    "class": "IntroAction",
                                    "config": {"singleton": True},
                                },
                            },
                        },
                    ]
                )
            )
        )
        manager = SimpleNamespace(
            deregister_action=AsyncMock(return_value=True), id="n.Actions.1"
        )

        class FakeAgent:
            id = "n.Agent.001"

            async def nodes(self, node):
                if node == "Actions":
                    return [manager]
                return []

        agent = FakeAgent()
        action = SimpleNamespace(id="n.Action.002")

        live_state = {
            "phase": graph_repair_job.PH_SCHEMA_SINGLETON_ACTIONS,
            "cursor": {"agent_ids": None, "agent_index": 0},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
        }

        with patch(
            "jvagent.core.agent.Agent.find", new=AsyncMock(return_value=[agent])
        ), patch(
            "jvagent.core.agent.Agent.get", new=AsyncMock(return_value=agent)
        ), patch(
            "jvagent.action.base.Action.get", new=AsyncMock(return_value=action)
        ), patch(
            "jvspatial.core.entities.node.Node.get", new=AsyncMock(return_value=None)
        ):
            out_live = await graph_repair_job._tick_schema_singleton_actions(
                context, live_state, limits
            )

        assert out_live is True
        manager.deregister_action.assert_awaited_once_with("n.Action.002")
        assert live_state["result"]["duplicate_singleton_actions_removed"] == 1
        assert live_state["phase"] == graph_repair_job.PH_DEAD_EDGES

        dry_state = {
            "phase": graph_repair_job.PH_SCHEMA_SINGLETON_ACTIONS,
            "cursor": {"agent_ids": None, "agent_index": 0},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": True,
        }
        manager.deregister_action.reset_mock()
        with patch(
            "jvagent.core.agent.Agent.find", new=AsyncMock(return_value=[agent])
        ), patch(
            "jvagent.core.agent.Agent.get", new=AsyncMock(return_value=agent)
        ), patch(
            "jvagent.action.base.Action.get", new=AsyncMock(return_value=action)
        ), patch(
            "jvspatial.core.entities.node.Node.get", new=AsyncMock(return_value=None)
        ):
            out_dry = await graph_repair_job._tick_schema_singleton_actions(
                context, dry_state, limits
            )

        assert out_dry is True
        manager.deregister_action.assert_not_called()
        assert dry_state["result"]["duplicate_singleton_actions_removed"] == 1

    @pytest.mark.asyncio
    async def test_orphan_user_with_conversations_not_deleted(self, temp_dir, test_db):
        """User with conversation history is preserved during orphan cleanup."""
        memory = await Memory.create()
        user = await User.create(memory_id=memory.id, user_id="u-preserve")
        await memory.connect(user)
        conversation = await Conversation.create(
            session_id="sess_preserve",
            user_id=user.user_id,
            channel="default",
        )
        await user.connect(conversation)
        interaction = await Interaction.create(
            conversation_id=conversation.id,
            utterance="hello",
        )
        await conversation.connect(interaction)
        await memory.disconnect(user)

        orphan_ids = {user.id}
        from jvagent.core.graph_repair_handlers import _reattach_user

        restored = await _reattach_user(
            get_default_context(), user, orphan_ids, dry_run=False
        )
        assert restored is True
        assert user.id not in orphan_ids
        assert await User.get(user.id) is not None
        assert await Conversation.get(conversation.id) is not None
        assert await Interaction.get(interaction.id) is not None

    @pytest.mark.asyncio
    async def test_orphan_delete_skips_user_with_incoming_edge(self, temp_dir, test_db):
        """Orphan delete phase should skip protected memory entities with incoming edges."""
        UserNode = type("User", (), {})
        node = UserNode()
        node.id = "n.User.001"
        node.edges = AsyncMock(return_value=[SimpleNamespace(id="e.1")])
        node.delete = AsyncMock()
        context = SimpleNamespace(get=AsyncMock(return_value=node))
        state = {
            "cursor": {"orphan_ids": [node.id], "delete_index": 0},
            "result": graph_repair_job._new_result_counters(),
            "dry_run": False,
            "phase": graph_repair_job.PH_ORPHANS_DELETE,
        }
        limits = graph_repair_job.RepairLimits(batch_size=10, max_seconds=5.0)

        out = await graph_repair_job._tick_orphans_delete(context, state, limits)

        assert out is True
        node.delete.assert_not_awaited()
        assert state["result"]["orphaned_nodes_deleted"] == 0
