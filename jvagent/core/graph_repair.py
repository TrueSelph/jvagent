"""Agent graph repair utility for jvagent.

Validates graph structure, removes dead edges, reattaches or removes orphaned
nodes, and syncs node-edge references. Memory repair (all agents) runs before
graph repair: a full Memory counter reconcile, then per-agent ``repair_memory``.
After structural repair,
interaction limit pruning runs for each agent's Memory (users, then their
conversations), last in the pipeline.
"""

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from jvspatial.core import Edge, Node, Root, get_default_context

logger = logging.getLogger(__name__)


async def repair_agent_graph(
    dry_run: bool = False,
    recent_minutes: Optional[int] = None,
    *,
    max_seconds: float = 30.0,
    batch_size: int = 500,
) -> Dict[str, Any]:
    """Run one bounded wave of app-wide graph repair with persisted state.

    Callers should re-invoke this API until ``status`` becomes ``completed``.
    Progress is stored in a temporary ``RepairState`` node attached to ``App``.
    """
    from jvagent.core.app import App
    from jvagent.core.graph_repair_job import (
        PH_DONE,
        STATE_VERSION,
        RepairLimits,
        _build_message,
        _initial_session_state,
        run_repair_session,
        state_from_dict,
        state_to_dict,
    )
    from jvagent.core.repair_state import RepairState

    limits = RepairLimits(batch_size=batch_size, max_seconds=max_seconds)
    app = await App.get()
    # App.get() uses a class-level cache; across context switches (tests, workers)
    # the cached node can point at a different database. Re-resolve if stale.
    if app is not None:
        live_app = await get_default_context().get(Node, app.id)
        if live_app is None:
            App.clear_cache()
            app = await App.get()
    started_at = datetime.now(timezone.utc)

    if app is None:
        state = _initial_session_state(dry_run, recent_minutes)
        state = await run_repair_session(state, limits)
        return _build_http_response(state, _build_message, PH_DONE, started_at)

    async with RepairState._lock:
        repair_state = await RepairState.current(app)
        if repair_state and (
            repair_state.dry_run != dry_run
            or (
                recent_minutes is not None
                and repair_state.recent_minutes != recent_minutes
            )
            or repair_state.version != STATE_VERSION
        ):
            await repair_state.finish()
            repair_state = None

        if repair_state is None:
            repair_state = await RepairState.begin(
                app,
                dry_run=dry_run,
                recent_minutes=recent_minutes,
                version=STATE_VERSION,
            )
            state = _initial_session_state(dry_run, recent_minutes)
        else:
            state = state_from_dict(
                {
                    "v": repair_state.version,
                    "phase": repair_state.phase,
                    "cursor": repair_state.cursor,
                    "result": repair_state.result,
                    "dry_run": repair_state.dry_run,
                    "recent_minutes": repair_state.recent_minutes,
                },
                dry_run=dry_run,
                recent_minutes=recent_minutes,
            )
        started_at = repair_state.started_at or started_at

        state = await run_repair_session(state, limits)
        payload = state_to_dict(state)

        if state.get("phase") == PH_DONE:
            await repair_state.finish()
        else:
            await repair_state.save_progress(
                phase=payload["phase"],
                cursor=payload["cursor"],
                result=payload["result"],
            )

    return _build_http_response(state, _build_message, PH_DONE, started_at)


def _build_http_response(
    state: Dict[str, Any], build_message: Any, done_phase: str, started_at: datetime
) -> Dict[str, Any]:
    """Convert internal state to API payload."""
    now = datetime.now(timezone.utc)
    started = (
        started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    )
    out = dict(state.get("result") or {})
    out["message"] = build_message(state)
    out["status"] = "completed" if state.get("phase") == done_phase else "in_progress"
    out["phase"] = state.get("phase", done_phase)
    out["started_at"] = started.isoformat()
    out["elapsed_seconds"] = max(0.0, (now - started).total_seconds())
    if state.get("dry_run"):
        out["dry_run"] = True
    return out


async def _reconcile_all_memory_counters() -> int:
    """Run _recalculate_counters on every Memory."""
    from jvagent.memory.manager import Memory

    total_fixed = 0
    for mem in await Memory.find({}):
        total_fixed += await mem._recalculate_counters()
    return total_fixed


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
        "conversation_branch_edges_removed": 0,
        "counters_fixed": 0,
    }
    aggregated["counters_fixed"] += await _reconcile_all_memory_counters()

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
            "conversation_branch_edges_removed",
            "counters_fixed",
        ):
            aggregated[key] += repair.get(key, 0)

    return aggregated


async def _run_interaction_pruning_all_agents() -> Dict[str, Any]:
    """Apply interaction_limit sync and pruning for each agent's Memory users.

    Iterates every User connected to each Memory, then each User's
    conversations. Caller must not invoke when repair_agent_graph(dry_run=True).

    Returns:
        Dict with interactions_pruned (total interactions removed).
    """
    from jvagent.core.agent import Agent

    total_pruned = 0
    agents: List[Any] = await Agent.find({})
    for agent in agents:
        memory = await agent.get_memory()
        if not memory:
            continue
        total_pruned += (
            await memory.apply_interaction_limit_pruning_for_connected_users()
        )

    return {"interactions_pruned": total_pruned}


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
    queue = deque([root])

    while queue:
        node = queue.popleft()
        try:
            neighbors = await node.nodes(direction="both")
            for neighbor in neighbors:
                if neighbor.id not in reachable:
                    reachable.add(neighbor.id)
                    queue.append(neighbor)
        except Exception as e:
            logger.debug("Error traversing from %s: %s", node.id, e)

    return reachable


async def _compute_reachable_nodes_excluding_root(context: Any, root: Node) -> Set[str]:
    """Return reachable node IDs from root, excluding the global Root node id."""
    reachable = await _compute_reachable_nodes(context, root)
    reachable.discard("n.Root.root")
    return reachable


async def _compute_reachable_nodes_below(context: Any, root: Node) -> Set[str]:
    """BFS from root following outgoing edges only.

    Used by dedupe heuristics to score how much descendant data (for example,
    Memory -> User -> Conversation -> Interaction) exists under a candidate.
    """
    reachable: Set[str] = {root.id}
    queue = deque([root])

    while queue:
        node = queue.popleft()
        try:
            neighbors = await node.nodes(direction="out")
            for neighbor in neighbors:
                if neighbor.id not in reachable:
                    reachable.add(neighbor.id)
                    queue.append(neighbor)
        except Exception as e:
            logger.debug("Error traversing descendants from %s: %s", node.id, e)

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
    return await _reattach_orphans_chunk(context, list(orphan_ids), orphan_ids, dry_run)


async def _reattach_orphans_chunk(
    context: Any,
    node_ids: List[str],
    orphan_ids: Set[str],
    dry_run: bool,
) -> int:
    """Reattach handlers for a subset of orphan node ids (batched repair)."""
    from jvagent.action.base import Action
    from jvagent.core.graph_repair_handlers import get_reattach_handler

    reattached = 0

    for node_id in node_ids:
        try:
            node = await context.get(Node, node_id)
            if not node:
                continue

            entity_name = node.__class__.__name__

            if entity_name == "Interaction":
                continue

            handler = get_reattach_handler(entity_name)
            if not handler and isinstance(node, Action):
                handler = get_reattach_handler("Action")

            if handler:
                if await handler(context, node, orphan_ids, dry_run):
                    reattached += 1

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

    from jvagent.memory.interaction import interaction_sort_key

    for conv_id, interactions in by_conv.items():
        interactions.sort(key=interaction_sort_key)
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
