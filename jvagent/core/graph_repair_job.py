"""Stateless batched graph repair (in-memory only, optional client-held cursor).

Each :func:`run_repair_session` call runs bounded work. If it stops before
``PH_DONE``, the response includes ``next_repair_cursor`` (base64url JSON).
Pass that value back as ``repair_cursor`` on the next call to continue without
server-side job files or ``job_id``. Omit the cursor to restart from the
beginning (already-repaired graph entities are naturally skipped via queries).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

SORT_ID_ASC: List[Tuple[str, int]] = [("id", 1)]
CURSOR_VERSION = 1

# Phases (ordered)
PH_MEMORY_COUNTERS = "memory_counters"
PH_MEMORY_AGENTS = "memory_agents"
PH_DEAD_EDGES = "dead_edges"
PH_SYNC_PREPARE = "sync_prepare"
PH_SYNC_APPLY = "sync_apply"
PH_ORPHANS_LIST_NODES = "orphans_list_nodes"
PH_ORPHANS_BFS = "orphans_bfs"
PH_ORPHANS_REATTACH = "orphans_reattach"
PH_ORPHANS_INTERACTION = "orphans_interaction_reattach"
PH_ORPHANS_DELETE = "orphans_delete"
PH_DUP_PREPARE = "dup_prepare"
PH_DUP_APPLY = "dup_apply"
PH_PRUNE_AGENTS = "prune_agents"
PH_DONE = "done"


@dataclass
class RepairLimits:
    """Bounds for one repair HTTP invocation (may run many internal batches)."""

    batch_size: int
    max_seconds: Optional[float]


def _new_result_counters() -> Dict[str, Any]:
    return {
        "memory_repair_agents": 0,
        "orphaned_interactions_deleted": 0,
        "orphaned_users_reconnected": 0,
        "dual_edges_removed": 0,
        "conversation_first_edges_restored": 0,
        "conversation_branch_edges_removed": 0,
        "dead_edges_removed": 0,
        "orphaned_nodes_reattached": 0,
        "orphaned_nodes_deleted": 0,
        "node_edge_ids_synced": 0,
        "duplicate_edges_removed": 0,
        "interactions_pruned": 0,
        "counters_fixed": 0,
    }


def _initial_session_state(
    dry_run: bool,
    recent_minutes: Optional[int],
) -> Dict[str, Any]:
    return {
        "phase": PH_MEMORY_COUNTERS if not dry_run else PH_DEAD_EDGES,
        "dry_run": dry_run,
        "recent_minutes": recent_minutes,
        "result": _new_result_counters(),
        "cursor": {},
    }


def encode_repair_cursor(state: Dict[str, Any]) -> str:
    """Serialize resumable session slice for the client (opaque string)."""
    payload = {
        "v": CURSOR_VERSION,
        "phase": state["phase"],
        "cursor": state.get("cursor") or {},
        "result": state.get("result") or _new_result_counters(),
        "dry_run": bool(state.get("dry_run")),
        "recent_minutes": state.get("recent_minutes"),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_repair_cursor(
    repair_cursor: Optional[str],
    dry_run: bool,
    recent_minutes: Optional[int],
) -> Dict[str, Any]:
    """Restore session from client cursor, or start fresh."""
    if not repair_cursor or not repair_cursor.strip():
        return _initial_session_state(dry_run, recent_minutes)
    try:
        raw = base64.urlsafe_b64decode(repair_cursor.strip().encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Invalid repair_cursor, restarting repair: %s", e)
        return _initial_session_state(dry_run, recent_minutes)
    if payload.get("v") != CURSOR_VERSION:
        logger.warning("Unknown repair_cursor version, restarting repair")
        return _initial_session_state(dry_run, recent_minutes)
    if bool(payload.get("dry_run")) != dry_run:
        logger.warning("repair_cursor dry_run mismatch, restarting repair")
        return _initial_session_state(dry_run, recent_minutes)
    return {
        "phase": payload["phase"],
        "dry_run": dry_run,
        "recent_minutes": (
            recent_minutes
            if recent_minutes is not None
            else payload.get("recent_minutes")
        ),
        "result": payload.get("result") or _new_result_counters(),
        "cursor": payload.get("cursor") or {},
    }


async def _find_edges_page(
    context: Any, after_id: Optional[str], batch_size: int
) -> List[Dict[str, Any]]:
    db = context.database
    if after_id:
        q: Dict[str, Any] = {"id": {"$gt": after_id}}
    else:
        q = {}
    return await db.find("edge", q, limit=batch_size, sort=SORT_ID_ASC)


async def _find_nodes_page(
    context: Any, after_id: Optional[str], batch_size: int
) -> List[Dict[str, Any]]:
    db = context.database
    if after_id:
        q: Dict[str, Any] = {"id": {"$gt": after_id}}
    else:
        q = {}
    return await db.find("node", q, limit=batch_size, sort=SORT_ID_ASC)


async def _tick_memory_counters(state: Dict[str, Any], limits: RepairLimits) -> bool:
    from jvagent.core.graph_repair import _reconcile_all_memory_counters

    res = state["result"]
    fixed = await _reconcile_all_memory_counters()
    res["counters_fixed"] = res.get("counters_fixed", 0) + fixed
    state["phase"] = PH_MEMORY_AGENTS
    state["cursor"] = {"agent_index": 0, "agent_ids": None}
    return True


async def _tick_memory_agents(state: Dict[str, Any], limits: RepairLimits) -> bool:
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    if cur.get("agent_ids") is None:
        agents = await Agent.find({})
        cur["agent_ids"] = [a.id for a in agents]
    agent_ids: List[str] = cur["agent_ids"]
    idx = int(cur.get("agent_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    processed = 0
    res = state["result"]

    while idx < len(agent_ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break

        agent = await Agent.get(agent_ids[idx])
        idx += 1
        processed += 1
        if not agent:
            continue
        memory = await agent.get_memory()
        if not memory:
            continue
        repair = await memory.repair_memory(recent_minutes=state.get("recent_minutes"))
        res["memory_repair_agents"] = res.get("memory_repair_agents", 0) + 1
        for key in (
            "orphaned_interactions_deleted",
            "orphaned_users_reconnected",
            "dual_edges_removed",
            "conversation_first_edges_restored",
            "conversation_branch_edges_removed",
            "counters_fixed",
        ):
            res[key] = res.get(key, 0) + repair.get(key, 0)

    cur["agent_index"] = idx
    if idx >= len(agent_ids):
        state["phase"] = PH_DEAD_EDGES
        state["cursor"] = {"last_edge_id": ""}
    return True


async def _tick_dead_edges(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Edge, Node

    cur = state["cursor"]
    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    removed = 0
    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_SYNC_PREPARE
        state["cursor"] = {
            "last_edge_id": "",
            "acc_node_edges": {},
            "acc_valid_ids": [],
        }
        return True

    for data in page:
        source_id = data.get("source", "")
        target_id = data.get("target", "")
        eid = data.get("id", "")

        async def try_delete(edge_data: dict = data, edge_id: str = eid) -> int:
            try:
                edge = await context._deserialize_entity(Edge, edge_data)
                if edge:
                    await context.delete(edge, cascade=False)
                    return 1
            except Exception as e:
                logger.warning("Failed to delete dead edge %s: %s", edge_id, e)
            return 0

        if not source_id or not target_id:
            if not state["dry_run"]:
                removed += await try_delete()
            else:
                removed += 1
            continue

        source_node = await context.get(Node, source_id)
        target_node = await context.get(Node, target_id)
        if source_node is None or target_node is None:
            if not state["dry_run"]:
                removed += await try_delete()
            else:
                removed += 1

    state["result"]["dead_edges_removed"] += removed
    last_id = page[-1].get("id", "")
    cur["last_edge_id"] = last_id
    if len(page) < batch:
        state["phase"] = PH_SYNC_PREPARE
        state["cursor"] = {
            "last_edge_id": "",
            "acc_node_edges": {},
            "acc_valid_ids": [],
        }
    return True


async def _tick_sync_prepare(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    """Accumulate node->edge ids and valid edge ids from paged edges."""
    cur = state["cursor"]
    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    acc: Dict[str, List[str]] = {
        k: list(v) for k, v in cur.get("acc_node_edges", {}).items()
    }
    valid: Set[str] = set(cur.get("acc_valid_ids", []))

    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_SYNC_APPLY
        state["cursor"] = {
            "last_node_id": "",
            "acc_node_edges": acc,
            "acc_valid_ids": sorted(valid),
        }
        return True

    for data in page:
        eid = data.get("id")
        source = data.get("source")
        target = data.get("target")
        if eid:
            valid.add(eid)
        if eid and source:
            acc.setdefault(source, []).append(eid)
        if eid and target:
            acc.setdefault(target, []).append(eid)

    cur["last_edge_id"] = page[-1].get("id", "")
    cur["acc_node_edges"] = {k: sorted(set(v)) for k, v in acc.items()}
    cur["acc_valid_ids"] = sorted(valid)
    if len(page) < batch:
        state["phase"] = PH_SYNC_APPLY
        state["cursor"] = {
            "last_node_id": "",
            "acc_node_edges": cur["acc_node_edges"],
            "acc_valid_ids": cur["acc_valid_ids"],
        }
    return True


async def _tick_sync_apply(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Node

    cur = state["cursor"]
    acc = {k: set(v) for k, v in cur.get("acc_node_edges", {}).items()}
    valid_ids = set(cur.get("acc_valid_ids", []))
    last = cur.get("last_node_id") or ""
    batch = limits.batch_size
    synced = 0

    page = await _find_nodes_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_ORPHANS_LIST_NODES
        state["cursor"] = {"last_node_id": "", "all_node_ids": []}
        return True

    dry = state["dry_run"]
    for data in page:
        node_id = data.get("id")
        if not node_id:
            continue
        current_edge_ids = set(data.get("edges", []))
        expected = acc.get(node_id, set())
        valid_current = current_edge_ids & valid_ids
        new_edge_ids = valid_current | expected
        if set(current_edge_ids) != new_edge_ids:
            if not dry:
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

    state["result"]["node_edge_ids_synced"] += synced
    cur["last_node_id"] = page[-1].get("id", "")
    if len(page) < batch:
        state["phase"] = PH_ORPHANS_LIST_NODES
        state["cursor"] = {
            "last_node_id": "",
            "all_node_ids": [],
        }
    return True


async def _tick_orphans_list_nodes(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    cur = state["cursor"]
    last = cur.get("last_node_id") or ""
    batch = limits.batch_size
    all_ids: List[str] = list(cur.get("all_node_ids", []))

    page = await _find_nodes_page(context, last if last else None, batch)
    if not page:
        from jvspatial.core import Root

        root = await Root.get()
        rid = root.id if root else "n.Root.root"
        state["phase"] = PH_ORPHANS_BFS
        state["cursor"] = {
            "all_node_ids": sorted(set(all_ids)),
            "bfs_queue": [rid],
            "bfs_seen": [],
        }
        return True

    for data in page:
        nid = data.get("id")
        if nid:
            all_ids.append(nid)
    cur["all_node_ids"] = all_ids
    cur["last_node_id"] = page[-1].get("id", "")
    if len(page) < batch:
        state["phase"] = PH_ORPHANS_BFS
        from jvspatial.core import Root

        root = await Root.get()
        state["cursor"] = {
            "all_node_ids": sorted(set(all_ids)),
            "bfs_queue": [root.id],
            "bfs_seen": [],
        }
    return True


async def _tick_orphans_bfs(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Node, Root

    cur = state["cursor"]
    queue: List[str] = list(cur.get("bfs_queue", []))
    seen: Set[str] = set(cur.get("bfs_seen", []) or [])
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    steps = 0

    while queue and steps < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        steps += 1
        try:
            node = await context.get(Node, nid)
            if not node:
                continue
            neighbors = await node.nodes(direction="both")
            for nb in neighbors:
                if nb.id not in seen:
                    queue.append(nb.id)
        except Exception as e:
            logger.debug("BFS error at %s: %s", nid, e)

    cur["bfs_queue"] = queue
    cur["bfs_seen"] = sorted(seen)
    if not queue:
        root = await Root.get()
        all_ids = set(cur.get("all_node_ids", []))
        reachable = set(seen)
        root_id = root.id if root else "n.Root.root"
        orphan_ids = sorted(all_ids - reachable)
        if root_id in orphan_ids:
            orphan_ids.remove(root_id)
        state["phase"] = PH_ORPHANS_REATTACH
        state["cursor"] = {"orphan_ids": orphan_ids, "orphan_index": 0}
    return True


async def _tick_orphans_reattach(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvagent.core.graph_repair import _reattach_orphans_chunk

    cur = state["cursor"]
    oids: List[str] = cur.get("orphan_ids", [])
    idx = int(cur.get("orphan_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    dry = state["dry_run"]
    orphan_set = set(oids)
    reattached = 0

    while idx < len(oids):
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        chunk = oids[idx : idx + batch]
        if not chunk:
            break
        n = await _reattach_orphans_chunk(context, chunk, orphan_set, dry)
        reattached += n
        idx += len(chunk)

    cur["orphan_index"] = idx
    state["result"]["orphaned_nodes_reattached"] += reattached
    if idx >= len(oids):
        state["phase"] = PH_ORPHANS_INTERACTION
        state["cursor"] = {"orphan_ids": oids}
    return True


async def _tick_orphans_interaction(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvagent.core.graph_repair import _reattach_interaction_orphans

    oids = set(state["cursor"].get("orphan_ids", []))
    dry = state["dry_run"]
    n = await _reattach_interaction_orphans(context, oids, dry)
    state["result"]["orphaned_nodes_reattached"] += n
    state["phase"] = PH_ORPHANS_DELETE
    state["cursor"] = {"orphan_ids": sorted(oids)}
    return True


async def _tick_orphans_delete(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Node

    cur = state["cursor"]
    oids: List[str] = cur.get("orphan_ids", [])
    idx = int(cur.get("delete_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    dry = state["dry_run"]
    root_id = "n.Root.root"
    deleted = 0

    while idx < len(oids):
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        if deleted >= batch:
            break
        node_id = oids[idx]
        idx += 1
        if node_id == root_id:
            continue
        try:
            node = await context.get(Node, node_id)
            if node and not dry:
                await node.delete(cascade=True)
                deleted += 1
            elif node and dry:
                deleted += 1
        except Exception as e:
            logger.warning("Failed to delete orphan node %s: %s", node_id, e)

    cur["delete_index"] = idx
    state["result"]["orphaned_nodes_deleted"] += deleted
    if idx >= len(oids):
        state["phase"] = PH_DUP_PREPARE
        state["cursor"] = {"last_edge_id": "", "dup_by_key": {}}
    return True


async def _tick_dup_prepare(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    cur = state["cursor"]
    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    dup_by_key: Dict[str, List[str]] = {
        k: list(v) for k, v in cur.get("dup_by_key", {}).items()
    }

    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        keys = sorted(k for k, v in dup_by_key.items() if len(v) > 1)
        state["phase"] = PH_DUP_APPLY
        state["cursor"] = {
            "dup_keys": keys,
            "dup_key_index": 0,
            "dup_by_key": dup_by_key,
        }
        return True

    for data in page:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        source = data.get("source")
        target = data.get("target")
        eid = data.get("id")
        if source and target and eid:
            key = f"{source}\n{target}"
            dup_by_key.setdefault(key, []).append(eid)

    for k in list(dup_by_key.keys()):
        dup_by_key[k] = sorted(set(dup_by_key[k]))

    cur["last_edge_id"] = page[-1].get("id", "")
    cur["dup_by_key"] = dup_by_key
    if len(page) < batch:
        keys = sorted(k for k, v in dup_by_key.items() if len(v) > 1)
        state["phase"] = PH_DUP_APPLY
        state["cursor"] = {
            "dup_keys": keys,
            "dup_key_index": 0,
            "dup_by_key": dup_by_key,
        }
    return True


async def _tick_dup_apply(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Edge, Node

    cur = state["cursor"]
    keys: List[str] = cur.get("dup_keys", [])
    ki = int(cur.get("dup_key_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    dry = state["dry_run"]
    removed = 0
    processed_keys = 0

    while ki < len(keys) and processed_keys < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        key = keys[ki]
        ki += 1
        processed_keys += 1
        src, _, tgt = key.partition("\n")
        group_ids = list(cur.get("dup_by_key", {}).get(key, []))
        if len(group_ids) <= 1:
            continue
        for dup_id in group_ids[1:]:
            dup_data = await context.database.get("edge", dup_id)
            if not dup_data:
                continue
            if not dry:
                try:
                    edge = await context._deserialize_entity(Edge, dup_data)
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
                    logger.warning("Failed to remove duplicate edge %s: %s", dup_id, e)
            else:
                removed += 1
        _ = (src, tgt)

    cur["dup_key_index"] = ki
    state["result"]["duplicate_edges_removed"] += removed
    if ki >= len(keys):
        if not state["dry_run"]:
            state["phase"] = PH_PRUNE_AGENTS
            state["cursor"] = {"prune_agent_index": 0, "prune_agent_ids": None}
        else:
            state["phase"] = PH_DONE
            state["cursor"] = {}
    return True


async def _tick_prune_agents(state: Dict[str, Any], limits: RepairLimits) -> bool:
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    if cur.get("prune_agent_ids") is None:
        agents = await Agent.find({})
        cur["prune_agent_ids"] = [a.id for a in agents]
    ids = cur["prune_agent_ids"]
    idx = int(cur.get("prune_agent_index", 0))
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    batch = limits.batch_size
    processed = 0
    while idx < len(ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        agent = await Agent.get(ids[idx])
        idx += 1
        processed += 1
        if agent:
            memory = await agent.get_memory()
            if memory:
                pruned = (
                    await memory.apply_interaction_limit_pruning_for_connected_users()
                )
                state["result"]["interactions_pruned"] += pruned
    cur["prune_agent_index"] = idx
    if idx >= len(ids):
        state["phase"] = PH_DONE
        state["cursor"] = {}
    return processed > 0 or idx >= len(ids)


def _build_message(state: Dict[str, Any]) -> str:
    r = state["result"]
    parts = []
    if r.get("memory_repair_agents"):
        parts.append(f"memory repaired for {r['memory_repair_agents']} agent(s)")
    if r.get("orphaned_interactions_deleted"):
        parts.append(f"{r['orphaned_interactions_deleted']} interaction(s) deleted")
    if r.get("orphaned_users_reconnected"):
        parts.append(f"{r['orphaned_users_reconnected']} user(s) reconnected")
    if r.get("dual_edges_removed"):
        parts.append(f"{r['dual_edges_removed']} dual edge(s) removed")
    if r.get("conversation_first_edges_restored"):
        parts.append(
            f"{r['conversation_first_edges_restored']} conv-first edge(s) restored"
        )
    if r.get("conversation_branch_edges_removed"):
        parts.append(
            f"{r['conversation_branch_edges_removed']} conv-branch edge(s) removed"
        )
    if r.get("dead_edges_removed"):
        parts.append(f"{r['dead_edges_removed']} dead edge(s) removed")
    if r.get("orphaned_nodes_reattached"):
        parts.append(f"{r['orphaned_nodes_reattached']} orphan(s) reattached")
    if r.get("orphaned_nodes_deleted"):
        parts.append(f"{r['orphaned_nodes_deleted']} orphan(s) deleted")
    if r.get("node_edge_ids_synced"):
        parts.append(f"{r['node_edge_ids_synced']} node(s) edge_ids synced")
    if r.get("duplicate_edges_removed"):
        parts.append(f"{r['duplicate_edges_removed']} duplicate edge(s) removed")
    if r.get("interactions_pruned"):
        parts.append(
            f"{r['interactions_pruned']} interaction(s) pruned (rolling limit)"
        )
    msg = "Repair completed: " + ", ".join(parts) if parts else "No repairs needed"
    if state.get("dry_run"):
        msg = "[DRY RUN] " + msg
    return msg


async def run_repair_session(
    dry_run: bool,
    recent_minutes: Optional[int],
    limits: RepairLimits,
    repair_cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one bounded repair wave; return aggregates and optional ``next_repair_cursor``."""
    from jvspatial.core import get_default_context

    state = decode_repair_cursor(repair_cursor, dry_run, recent_minutes)
    context = get_default_context()
    deadline = time.monotonic() + (limits.max_seconds or 86400.0)

    while time.monotonic() < deadline:
        phase = state["phase"]
        if phase == PH_DONE:
            break
        before = json.dumps(state.get("cursor"), sort_keys=True)
        phase_before = state["phase"]
        if phase == PH_MEMORY_COUNTERS:
            await _tick_memory_counters(state, limits)
        elif phase == PH_MEMORY_AGENTS:
            await _tick_memory_agents(state, limits)
        elif phase == PH_DEAD_EDGES:
            await _tick_dead_edges(context, state, limits)
        elif phase == PH_SYNC_PREPARE:
            await _tick_sync_prepare(context, state, limits)
        elif phase == PH_SYNC_APPLY:
            await _tick_sync_apply(context, state, limits)
        elif phase == PH_ORPHANS_LIST_NODES:
            await _tick_orphans_list_nodes(context, state, limits)
        elif phase == PH_ORPHANS_BFS:
            await _tick_orphans_bfs(context, state, limits)
        elif phase == PH_ORPHANS_REATTACH:
            await _tick_orphans_reattach(context, state, limits)
        elif phase == PH_ORPHANS_INTERACTION:
            await _tick_orphans_interaction(context, state, limits)
        elif phase == PH_ORPHANS_DELETE:
            await _tick_orphans_delete(context, state, limits)
        elif phase == PH_DUP_PREPARE:
            await _tick_dup_prepare(context, state, limits)
        elif phase == PH_DUP_APPLY:
            await _tick_dup_apply(context, state, limits)
        elif phase == PH_PRUNE_AGENTS:
            await _tick_prune_agents(state, limits)
        else:
            break
        after = json.dumps(state.get("cursor"), sort_keys=True)
        if before == after and phase_before == state["phase"]:
            break
        if state["phase"] == PH_DONE:
            break

    status = "completed" if state["phase"] == PH_DONE else "in_progress"
    out = dict(state["result"])
    out["message"] = _build_message(state)
    out["status"] = status
    out["phase"] = state["phase"]
    if dry_run:
        out["dry_run"] = True
    if status == "in_progress":
        out["next_repair_cursor"] = encode_repair_cursor(state)
    return out


async def run_repair_until_done(
    dry_run: bool,
    recent_minutes: Optional[int],
    limits: RepairLimits,
) -> Dict[str, Any]:
    """Run repair in-process until completed (no client cursor; in-memory only)."""
    cursor: Optional[str] = None
    while True:
        out = await run_repair_session(
            dry_run, recent_minutes, limits, repair_cursor=cursor
        )
        if out.get("status") == "completed":
            out.pop("next_repair_cursor", None)
            return out
        cursor = out.get("next_repair_cursor")
