"""Agent graph repair utility for jvagent.

Validates graph structure, removes dead edges, reattaches or removes orphaned
nodes, and syncs node-edge references. Memory repair (all agents) runs before
graph repair to ensure a clean memory state prior to structural validation.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from jvspatial.core import Edge, Node, Root, get_default_context

logger = logging.getLogger(__name__)


async def repair_agent_graph(
    dry_run: bool = False,
    recent_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Run memory repair (all agents) then agent graph repair procedures.

    Memory repair executes first for all agents to ensure a consistent memory
    state before structural graph validation. Graph repair then validates
    structure, removes dead edges, syncs node edge_ids, reattaches or removes
    orphaned nodes, and removes duplicate edges.

    Args:
        dry_run: If True, report issues without making changes.
        recent_minutes: Passed to memory repair to limit orphan interaction
            cleanup to last N minutes (None = all).

    Returns:
        Dict with memory_repair_agents, orphaned_interactions_deleted,
        orphaned_users_reconnected, dual_edges_removed,
        conversation_first_edges_restored, dead_edges_removed,
        orphaned_nodes_reattached, orphaned_nodes_deleted,
        node_edge_ids_synced, duplicate_edges_removed, message.
    """
    context = get_default_context()
    result = {
        "memory_repair_agents": 0,
        "orphaned_interactions_deleted": 0,
        "orphaned_users_reconnected": 0,
        "dual_edges_removed": 0,
        "conversation_first_edges_restored": 0,
        "dead_edges_removed": 0,
        "orphaned_nodes_reattached": 0,
        "orphaned_nodes_deleted": 0,
        "node_edge_ids_synced": 0,
        "duplicate_edges_removed": 0,
    }

    if dry_run:
        result["dry_run"] = True

    # 0. Memory repair for all agents (before graph repair)
    memory_result = None
    if not dry_run:
        memory_result = await _run_memory_repair_all_agents(recent_minutes)
        if memory_result:
            result.update(memory_result)

    # 1. Remove dead edges
    dead_removed = await _remove_dead_edges(context, dry_run)
    result["dead_edges_removed"] = dead_removed

    # 2. Sync node edge_ids
    synced = await _sync_node_edge_ids(context, dry_run)
    result["node_edge_ids_synced"] = synced

    # 3. Identify orphaned nodes and reattach or remove
    root = await Root.get()
    reachable = await _compute_reachable_nodes(context, root)
    all_node_ids = await _get_all_node_ids(context)
    orphan_ids = all_node_ids - reachable

    # Exclude Root from orphans
    root_id = getattr(Root, "id", "n.Root.root") if Root else "n.Root.root"
    orphan_ids.discard(root_id)

    reattached = await _reattach_orphans(context, orphan_ids, dry_run)
    reattached += await _reattach_interaction_orphans(context, orphan_ids, dry_run)
    result["orphaned_nodes_reattached"] = reattached

    deleted = await _remove_orphaned_nodes(context, orphan_ids, dry_run)
    result["orphaned_nodes_deleted"] = deleted

    # 4. Remove duplicate edges
    dup_removed = await _remove_duplicate_edges(context, dry_run)
    result["duplicate_edges_removed"] = dup_removed

    parts = []
    if memory_result:
        agents_repaired = memory_result.get("memory_repair_agents", 0)
        if agents_repaired:
            parts.append(f"memory repaired for {agents_repaired} agent(s)")
        if result.get("orphaned_interactions_deleted"):
            parts.append(
                f"{result['orphaned_interactions_deleted']} interaction(s) deleted"
            )
        if result.get("orphaned_users_reconnected"):
            parts.append(f"{result['orphaned_users_reconnected']} user(s) reconnected")
        if result.get("dual_edges_removed"):
            parts.append(f"{result['dual_edges_removed']} dual edge(s) removed")
        if result.get("conversation_first_edges_restored"):
            parts.append(
                f"{result['conversation_first_edges_restored']} conv-first edge(s) restored"
            )
    if dead_removed:
        parts.append(f"{dead_removed} dead edge(s) removed")
    if reattached:
        parts.append(f"{reattached} orphan(s) reattached")
    if deleted:
        parts.append(f"{deleted} orphan(s) deleted")
    if synced:
        parts.append(f"{synced} node(s) edge_ids synced")
    if dup_removed:
        parts.append(f"{dup_removed} duplicate edge(s) removed")

    result["message"] = (
        "Repair completed: " + ", ".join(parts) if parts else "No repairs needed"
    )
    if dry_run:
        result["message"] = "[DRY RUN] " + result["message"]

    return result


async def _run_memory_repair_all_agents(
    recent_minutes: Optional[int],
) -> Dict[str, Any]:
    """Run memory repair for every agent that has a Memory node.

    Args:
        recent_minutes: Passed to each agent's memory repair to limit orphan
            interaction cleanup to last N minutes (None = all).

    Returns:
        Aggregated dict with memory_repair_agents count and summed repair fields.
    """
    from jvagent.core.agent import Agent

    aggregated: Dict[str, Any] = {
        "memory_repair_agents": 0,
        "orphaned_interactions_deleted": 0,
        "orphaned_users_reconnected": 0,
        "dual_edges_removed": 0,
        "conversation_first_edges_restored": 0,
        "counters_fixed": 0,
    }

    agents: List[Any] = await Agent.find({})
    for agent in agents:
        memory = await agent.get_memory()
        if not memory:
            continue
        repair = await memory.repair_memory(recent_minutes=recent_minutes)
        aggregated["memory_repair_agents"] += 1
        for key in (
            "orphaned_interactions_deleted",
            "orphaned_users_reconnected",
            "dual_edges_removed",
            "conversation_first_edges_restored",
            "counters_fixed",
        ):
            aggregated[key] += repair.get(key, 0)

    return aggregated


async def _remove_dead_edges(context: Any, dry_run: bool) -> int:
    """Remove edges where source or target node does not exist."""
    edges_data = await context.database.find("edge", {})
    removed = 0

    for data in edges_data:
        source_id = data.get("source", "")
        target_id = data.get("target", "")

        if not source_id or not target_id:
            if not dry_run:
                try:
                    edge = await context._deserialize_entity(Edge, data)
                    if edge:
                        await context.delete(edge, cascade=False)
                        removed += 1
                except Exception as e:
                    logger.warning(
                        "Failed to delete dead edge %s: %s", data.get("id"), e
                    )
            else:
                removed += 1
            continue

        source_node = await context.get(Node, source_id)
        target_node = await context.get(Node, target_id)

        if source_node is None or target_node is None:
            if not dry_run:
                try:
                    edge = await context._deserialize_entity(Edge, data)
                    if edge:
                        await context.delete(edge, cascade=False)
                        removed += 1
                except Exception as e:
                    logger.warning(
                        "Failed to delete dead edge %s: %s", data.get("id"), e
                    )
            else:
                removed += 1

    return removed


async def _sync_node_edge_ids(context: Any, dry_run: bool) -> int:
    """Sync node edge_ids: remove stale, add missing from edges."""
    edges_data = await context.database.find("edge", {})
    nodes_data = await context.database.find("node", {})

    valid_edge_ids = {e.get("id") for e in edges_data if e.get("id")}
    node_to_edge_ids: Dict[str, Set[str]] = defaultdict(set)

    for data in edges_data:
        eid = data.get("id")
        source = data.get("source")
        target = data.get("target")
        if eid and source:
            node_to_edge_ids[source].add(eid)
        if eid and target:
            node_to_edge_ids[target].add(eid)

    synced = 0
    for data in nodes_data:
        node_id = data.get("id")
        if not node_id:
            continue

        current_edge_ids = set(data.get("edges", []))
        expected = node_to_edge_ids.get(node_id, set())
        valid_current = current_edge_ids & valid_edge_ids
        new_edge_ids = valid_current | expected

        if set(current_edge_ids) != new_edge_ids:
            if not dry_run:
                try:
                    node = await context._deserialize_entity(Node, data)
                    if node:
                        node.edge_ids = list(new_edge_ids)
                        await node.save()
                        synced += 1
                except Exception as e:
                    logger.warning(
                        "Failed to sync edge_ids for node %s: %s", node_id, e
                    )
            else:
                synced += 1

    return synced


async def _compute_reachable_nodes(context: Any, root: Node) -> Set[str]:
    """BFS from root to compute all reachable node IDs."""
    reachable: Set[str] = {root.id}
    queue = [root]

    while queue:
        node = queue.pop(0)
        try:
            neighbors = await node.nodes(direction="both")
            for neighbor in neighbors:
                if neighbor.id not in reachable:
                    reachable.add(neighbor.id)
                    queue.append(neighbor)
        except Exception as e:
            logger.debug("Error traversing from %s: %s", node.id, e)

    return reachable


async def _get_all_node_ids(context: Any) -> Set[str]:
    """Get all node IDs in the graph."""
    nodes_data = await context.database.find("node", {})
    return {n.get("id") for n in nodes_data if n.get("id")}


async def _reattach_orphans(
    context: Any,
    orphan_ids: Set[str],
    dry_run: bool,
) -> int:
    """Try to reattach orphaned nodes to their expected parents."""
    reattached = 0

    for node_id in list(orphan_ids):
        try:
            node = await context.get(Node, node_id)
            if not node:
                continue

            entity_name = node.__class__.__name__

            if entity_name == "App":
                root = await Root.get()
                if root and not await root.is_connected_to(node):
                    if not dry_run:
                        await root.connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node_id)

            elif entity_name == "Agents":
                from jvagent.core.app import App

                app_nodes = await App.find({})
                for app in app_nodes:
                    if not await app.is_connected_to(node):
                        if not dry_run:
                            await app.connect(node, direction="out")
                        reattached += 1
                        orphan_ids.discard(node_id)
                        break

            elif entity_name == "Agent":
                from jvagent.core.agents import Agents

                agents_nodes = await Agents.find({})
                for agents in agents_nodes:
                    if not await agents.is_connected_to(node):
                        if not dry_run:
                            await agents.connect(node, direction="out")
                        reattached += 1
                        orphan_ids.discard(node_id)
                        break

            elif entity_name == "Actions":
                from jvagent.core.agent import Agent

                agents_without_actions = []
                for a in await Agent.find({}):
                    actions_mgr = await a.node(node="Actions")
                    if not actions_mgr:
                        agents_without_actions.append(a)
                if len(
                    agents_without_actions
                ) == 1 and not await agents_without_actions[0].is_connected_to(node):
                    if not dry_run:
                        await agents_without_actions[0].connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node_id)

            elif entity_name == "Memory":
                from jvagent.core.agent import Agent

                agents_without_memory = []
                for a in await Agent.find({}):
                    mem = await a.node(node="Memory")
                    if not mem:
                        agents_without_memory.append(a)
                if len(agents_without_memory) == 1 and not await agents_without_memory[
                    0
                ].is_connected_to(node):
                    if not dry_run:
                        await agents_without_memory[0].connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node_id)

            elif entity_name == "User":
                from jvagent.memory.manager import Memory

                user_id = getattr(node, "user_id", None)
                if not user_id:
                    continue
                # Only reattach if the user has no edges at all (true orphan).
                # This avoids cross-agent contamination by not reconnecting
                # users that belong to another Memory.
                if node.edge_ids:
                    continue
                memories = await Memory.find({})
                for memory in memories:
                    connected_users = await memory.nodes(node="User")
                    if any(u.user_id == user_id for u in connected_users):
                        continue
                    if not await memory.is_connected_to(node):
                        if not dry_run:
                            await memory.connect(node, direction="out")
                        reattached += 1
                        orphan_ids.discard(node_id)
                        break

            elif entity_name == "Conversation":
                from jvagent.memory.user import User

                user_id = getattr(node, "user_id", None)
                if not user_id:
                    continue
                user = await User.find_one({"context.user_id": user_id})
                if user and not await user.is_connected_to(node):
                    if not dry_run:
                        await user.connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node_id)

            elif entity_name == "Interaction":
                continue

            else:
                from jvagent.action.base import Action
                from jvagent.core.agent import Agent

                if isinstance(node, Action):
                    action_agent_id = getattr(node, "agent_id", None)
                    if action_agent_id:
                        agent = await Agent.get(action_agent_id)
                        if agent:
                            actions_manager = await agent.get_actions_manager()
                            if (
                                actions_manager
                                and not await actions_manager.is_connected_to(node)
                            ):
                                if not dry_run:
                                    await actions_manager.connect(node, direction="out")
                                reattached += 1
                                orphan_ids.discard(node_id)

        except Exception as e:
            logger.debug("Could not reattach %s: %s", node_id, e)

    return reattached


async def _reattach_interaction_orphans(
    context: Any, orphan_ids: Set[str], dry_run: bool
) -> int:
    """Reattach orphan Interaction nodes in started_at order per conversation."""
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

    reattached = 0
    by_conv: Dict[str, list] = defaultdict(list)

    for node_id in list(orphan_ids):
        node = await context.get(Node, node_id)
        if not node or node.__class__.__name__ != "Interaction":
            continue
        conv_id = getattr(node, "conversation_id", None)
        if not conv_id:
            continue
        by_conv[conv_id].append(node)

    for conv_id, interactions in by_conv.items():
        interactions.sort(key=lambda n: (getattr(n, "started_at", None) or "", n.id))
        conversation = await Conversation.get(conv_id)
        if not conversation:
            continue
        first_existing = await conversation.get_first_interaction()
        prev = None
        if first_existing:
            current = first_existing
            while True:
                next_int = await current.get_next_interaction()
                if not next_int:
                    break
                current = next_int
            prev = current
        for node in interactions:
            if node.id not in orphan_ids:
                continue
            if prev is None:
                if not await conversation.is_connected_to(node):
                    if not dry_run:
                        await conversation.connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node.id)
            else:
                if not await prev.is_connected_to(node):
                    if not dry_run:
                        await prev.connect(node, direction="out")
                    reattached += 1
                    orphan_ids.discard(node.id)
            prev = node

    return reattached


async def _remove_orphaned_nodes(
    context: Any, orphan_ids: Set[str], dry_run: bool
) -> int:
    """Delete nodes that could not be reattached."""
    root_id = "n.Root.root"
    deleted = 0

    for node_id in orphan_ids:
        if node_id == root_id:
            continue
        try:
            node = await context.get(Node, node_id)
            if node and not dry_run:
                await node.delete(cascade=True)
                deleted += 1
            elif node and dry_run:
                deleted += 1
        except Exception as e:
            logger.warning("Failed to delete orphan node %s: %s", node_id, e)

    return deleted


async def _remove_duplicate_edges(context: Any, dry_run: bool) -> int:
    """Remove duplicate edges (same source, target)."""
    edges_data = await context.database.find("edge", {})
    by_key: Dict[tuple, list] = defaultdict(list)

    for data in edges_data:
        source = data.get("source")
        target = data.get("target")
        if source and target:
            key = (source, target)
            by_key[key].append(data)

    removed = 0
    for key, group in by_key.items():
        if len(group) <= 1:
            continue
        keep = group[0]
        for dup in group[1:]:
            if not dry_run:
                try:
                    edge = await context._deserialize_entity(Edge, dup)
                    if edge:
                        source_node = await context.get(Node, edge.source)
                        target_node = await context.get(Node, edge.target)
                        if source_node and edge.id in source_node.edge_ids:
                            source_node.edge_ids.remove(edge.id)
                            await source_node.save()
                        if target_node and edge.id in target_node.edge_ids:
                            target_node.edge_ids.remove(edge.id)
                            await target_node.save()
                        await context.delete(edge, cascade=False)
                        removed += 1
                except Exception as e:
                    logger.warning(
                        "Failed to remove duplicate edge %s: %s", dup.get("id"), e
                    )
            else:
                removed += 1

    return removed
