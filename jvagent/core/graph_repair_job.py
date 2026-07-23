"""Synchronous-step graph repair engine.

Each :func:`run_repair_session` call runs **exactly one** top-level phase tick
on a mutable repair state dict, then returns. Progress is persisted after each
tick (via :func:`jvagent.core.graph_repair.repair_agent_graph`), so the run
can fail or time out at any point and **resume** on the next invocation from
the stored ``phase`` / ``cursor``. Callers (HTTP, cron, Lambda) should re-invoke
until ``status == "completed"``; do not rely on one process finishing the full
pipeline in a single call.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Repair state version and phase identifiers (formerly in repair_phases/types.py)
STATE_VERSION = 3

PH_MEMORY_COUNTERS = "memory_counters"
PH_MEMORY_AGENTS = "memory_agents"
PH_SCHEMA_APP_DEDUPE = "schema_app_dedupe"
PH_SCHEMA_AGENT_DEDUPE = "schema_agent_dedupe"
PH_SCHEMA_ACTIONS_DEDUPE = "schema_actions_dedupe"
PH_SCHEMA_MEMORY_DEDUPE = "schema_memory_dedupe"
PH_SCHEMA_SINGLETON_ACTIONS = "schema_singleton_actions"
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
    """Bounds for one repair step (one phase tick); work inside the tick is batched."""

    batch_size: int
    max_seconds: Optional[float]


async def repair_checkpoint(state: Dict[str, Any]) -> None:
    """Flush persisted repair cursor when ``state['_checkpoint']`` is set."""
    fn = state.get("_checkpoint")
    if fn is not None and callable(fn):
        await fn(state)


# Memory repair functions (formerly in repair_phases/memory.py)
_MEMORY_AGENT_STEPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("cleanup_orphans", ("orphaned_interactions_deleted",)),
    (
        "repair_chain",
        (
            "dual_edges_removed",
            "conversation_first_edges_restored",
            "conversation_branch_edges_removed",
        ),
    ),
    ("reconnect_users", ("orphaned_users_reconnected",)),
    ("recalc_counters", ("counters_fixed",)),
)

_MEMORY_AGENT_STEP_MAX_ATTEMPTS = 3


async def tick_memory_counters(state: Dict[str, Any], limits: RepairLimits) -> bool:
    """Paged memory counter reconciliation with deadline awareness."""
    from jvagent.memory.manager import Memory

    cur = state["cursor"]
    if cur.get("mc_memory_ids") is None:
        memories = await Memory.find({})
        cur["mc_memory_ids"] = [m.id for m in memories]
        cur["mc_index"] = 0

    memory_ids: List[str] = cur["mc_memory_ids"]
    idx = int(cur.get("mc_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    fixed = 0
    processed = 0

    while idx < len(memory_ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break

        memory_id = memory_ids[idx]
        idx += 1
        processed += 1
        cur["mc_index"] = idx
        await repair_checkpoint(state)

        try:
            mem = await Memory.get(memory_id)
            if mem:
                fixed += await mem._recalculate_counters()
        except Exception:
            logger.warning(
                "repair_tick memory_counters: skipped %s (error)",
                memory_id,
                exc_info=True,
            )

    state["result"]["counters_fixed"] = state["result"].get("counters_fixed", 0) + fixed

    if idx >= len(memory_ids):
        state["phase"] = PH_MEMORY_AGENTS
        state["cursor"] = {"agent_index": 0, "agent_ids": None}
    return True


async def _run_memory_agent_step(
    memory: Any, step_key: str, recent_minutes: Optional[int]
) -> Dict[str, int]:
    if step_key == "cleanup_orphans":
        deleted = await memory._cleanup_orphaned_interactions(recent_minutes)
        return {"orphaned_interactions_deleted": int(deleted or 0)}
    if step_key == "repair_chain":
        dual, first, branch = await memory._repair_interaction_chain_invariants()
        return {
            "dual_edges_removed": int(dual or 0),
            "conversation_first_edges_restored": int(first or 0),
            "conversation_branch_edges_removed": int(branch or 0),
        }
    if step_key == "reconnect_users":
        reconnected = await memory._reconnect_orphaned_users()
        return {"orphaned_users_reconnected": int(reconnected or 0)}
    if step_key == "recalc_counters":
        fixed = await memory._recalculate_counters()
        return {"counters_fixed": int(fixed or 0)}
    return {}


def _agent_had_repair_activity(res: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    deltas = cur.get("_agent_deltas") or {}
    return any(v > 0 for v in deltas.values())


async def tick_memory_agents(state: Dict[str, Any], limits: RepairLimits) -> bool:
    """Repair per-agent Memory graphs with fine-grained resume support."""
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    if cur.get("agent_ids") is None:
        agents = await Agent.find({})
        cur["agent_ids"] = [a.id for a in agents]
    agent_ids: List[str] = cur["agent_ids"]
    idx = int(cur.get("agent_index", 0))
    step_idx = int(cur.get("agent_step", 0))
    step_attempts = int(cur.get("step_attempts", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    processed_steps = 0
    res = state["result"]

    while idx < len(agent_ids):
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        if processed_steps >= batch:
            break

        agent_id = agent_ids[idx]

        if step_idx >= len(_MEMORY_AGENT_STEPS):
            if _agent_had_repair_activity(res, cur):
                res["memory_repair_agents"] = res.get("memory_repair_agents", 0) + 1
            cur.pop("_agent_deltas", None)
            idx += 1
            step_idx = 0
            step_attempts = 0
            cur["agent_index"] = idx
            cur["agent_step"] = step_idx
            cur["step_attempts"] = step_attempts
            await repair_checkpoint(state)
            continue

        step_key, _ = _MEMORY_AGENT_STEPS[step_idx]

        if step_attempts >= _MEMORY_AGENT_STEP_MAX_ATTEMPTS:
            logger.warning(
                "repair_tick memory_agents: skipping step %s on agent %s after %d failed attempts",
                step_key,
                agent_id,
                step_attempts,
            )
            step_idx += 1
            step_attempts = 0
            cur["agent_step"] = step_idx
            cur["step_attempts"] = step_attempts
            await repair_checkpoint(state)
            continue

        step_attempts += 1
        cur["step_attempts"] = step_attempts
        await repair_checkpoint(state)

        deltas: Dict[str, int] = {}
        try:
            agent = await Agent.get(agent_id)
            if not agent:
                step_idx = len(_MEMORY_AGENT_STEPS)
            else:
                memory = await agent.get_memory()
                if not memory:
                    step_idx = len(_MEMORY_AGENT_STEPS)
                else:
                    deltas = await _run_memory_agent_step(
                        memory, step_key, state.get("recent_minutes")
                    )
        except Exception:
            logger.warning(
                "repair_tick memory_agents: step %s failed on agent %s (advancing)",
                step_key,
                agent_id,
                exc_info=True,
            )
            deltas = {}

        for key, delta in deltas.items():
            if delta:
                res[key] = res.get(key, 0) + delta
                agent_deltas = cur.setdefault("_agent_deltas", {})
                agent_deltas[key] = agent_deltas.get(key, 0) + delta

        if step_idx < len(_MEMORY_AGENT_STEPS):
            step_idx += 1
        step_attempts = 0
        cur["agent_step"] = step_idx
        cur["step_attempts"] = step_attempts
        processed_steps += 1
        await repair_checkpoint(state)

    if idx >= len(agent_ids):
        state["phase"] = PH_SCHEMA_APP_DEDUPE
        state["cursor"] = {}
    return True


# Backward-compatible private aliases for tests and external patch targets.
_tick_memory_counters = tick_memory_counters
_tick_memory_agents = tick_memory_agents

SORT_ID_ASC: List[Tuple[str, int]] = [("id", 1)]

# Full-graph orphan/dup/prune phases run after edge sync (post-listen). When
# JVAGENT_DEFER_REPAIR=1 these are skipped so cold start can return faster;
# schedule POST /graph/repair (or the repair scheduler) to run them later.
_OPTIONAL_POST_LISTEN_PHASES = frozenset(
    {
        PH_ORPHANS_LIST_NODES,
        PH_ORPHANS_BFS,
        PH_ORPHANS_REATTACH,
        PH_ORPHANS_INTERACTION,
        PH_ORPHANS_DELETE,
        PH_DUP_PREPARE,
        PH_DUP_APPLY,
        PH_PRUNE_AGENTS,
    }
)


def _defer_noncritical_repair() -> bool:
    import os

    from jvagent.core.env_resolver import parse_bool_env

    return parse_bool_env(os.getenv("JVAGENT_DEFER_REPAIR", ""))


def _apply_deferred_phase_skip(state: Dict[str, Any]) -> None:
    """Jump optional post-listen phases to done when defer env is set."""
    if not _defer_noncritical_repair():
        return
    if state.get("phase") in _OPTIONAL_POST_LISTEN_PHASES:
        state["phase"] = PH_DONE
        state["cursor"] = {}


def _repair_result_is_pristine(result: Optional[Dict[str, Any]]) -> bool:
    """True when ``result`` has only zero counters (never accumulated repair work)."""
    if not result:
        return True
    baseline = _new_result_counters()
    for k in baseline:
        v = result.get(k)
        if (v or 0) != 0:
            return False
    return all(not (k not in baseline and (v or 0) != 0) for k, v in result.items())


def _new_result_counters() -> Dict[str, Any]:
    return {
        "memory_repair_agents": 0,
        "orphaned_interactions_deleted": 0,
        "orphaned_users_reconnected": 0,
        "dual_edges_removed": 0,
        "conversation_first_edges_restored": 0,
        "conversation_branch_edges_removed": 0,
        "duplicate_apps_removed": 0,
        "duplicate_agents_removed": 0,
        "duplicate_actions_managers_removed": 0,
        "duplicate_memory_nodes_removed": 0,
        "duplicate_singleton_actions_removed": 0,
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
    import uuid

    return {
        "phase": PH_MEMORY_COUNTERS if not dry_run else PH_SCHEMA_APP_DEDUPE,
        "dry_run": dry_run,
        "recent_minutes": recent_minutes,
        "result": _new_result_counters(),
        "cursor": {},
        "run_id": uuid.uuid4().hex,
        "stall_count": 0,
    }


def state_to_dict(state: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize repair state for persistence."""
    return {
        "v": STATE_VERSION,
        "phase": state.get("phase", PH_DONE),
        "cursor": state.get("cursor") or {},
        "result": state.get("result") or _new_result_counters(),
        "dry_run": bool(state.get("dry_run")),
        "recent_minutes": state.get("recent_minutes"),
        "run_id": state.get("run_id") or "",
        "stall_count": int(state.get("stall_count", 0)),
    }


def state_from_dict(
    payload: Optional[Dict[str, Any]],
    *,
    dry_run: bool,
    recent_minutes: Optional[int],
) -> Dict[str, Any]:
    """Restore session state from persisted dict or start fresh."""
    if not payload:
        return _initial_session_state(dry_run, recent_minutes)
    if payload.get("v") != STATE_VERSION:
        logger.warning("Unknown repair state version, restarting repair")
        return _initial_session_state(dry_run, recent_minutes)
    if bool(payload.get("dry_run")) != dry_run:
        logger.warning("Repair state dry_run mismatch, restarting repair")
        return _initial_session_state(dry_run, recent_minutes)
    cursor_in = payload.get("cursor")
    if cursor_in is None:
        cur: Dict[str, Any] = {}
    else:
        cur = dict(cursor_in) if cursor_in else {}
    result = payload.get("result") or _new_result_counters()
    phase: str = str(payload.get("phase", PH_DONE))
    # Legacy: RepairState.begin used phase="done" (same string as PH_DONE) before
    # the first save_progress, so a timeout before the first checkpoint looked
    # "complete" and repair exited without work or deleted the state.
    if phase == PH_DONE and not cur and _repair_result_is_pristine(result):
        logger.warning(
            "Repair state had phase=done with empty cursor and no counters; "
            "remapping to the first work phase (unfinished session)"
        )
        phase = PH_SCHEMA_APP_DEDUPE if dry_run else PH_MEMORY_COUNTERS
    return {
        "phase": phase,
        "dry_run": dry_run,
        "recent_minutes": (
            recent_minutes
            if recent_minutes is not None
            else payload.get("recent_minutes")
        ),
        "result": result,
        "cursor": cur,
        "run_id": payload.get("run_id") or "",
        "stall_count": int(payload.get("stall_count", 0)),
    }


# _repair_checkpoint removed — call sites use repair_checkpoint directly


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


async def _entity_count(context: Any, entity: str) -> int:
    """Return the number of nodes with the given entity type using count."""
    return await context.database.count("node", {"entity": entity})


async def _tick_schema_app_dedupe(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvagent.core.app import App
    from jvagent.core.graph_repair import _compute_reachable_nodes_excluding_root

    # Fast-skip: if only one App node exists there cannot be duplicates.
    app_count = await _entity_count(context, "App")
    if app_count <= 1:
        state["phase"] = PH_SCHEMA_AGENT_DEDUPE
        state["cursor"] = {}
        return True

    apps = await App.find({})
    if len(apps) <= 1:
        state["phase"] = PH_SCHEMA_AGENT_DEDUPE
        state["cursor"] = {}
        return True

    scored: List[Tuple[int, str, Any]] = []
    for app in apps:
        reachable = await _compute_reachable_nodes_excluding_root(context, app)
        density = len(reachable)
        scored.append((density, app.id, app))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    keep_id = scored[0][1]
    removed = 0
    for _, _, app in scored[1:]:
        if app.id == keep_id:
            continue
        if not state["dry_run"]:
            await app.delete(cascade=True)
        removed += 1

    if removed:
        App.clear_cache()
    state["result"]["duplicate_apps_removed"] += removed
    state["phase"] = PH_SCHEMA_AGENT_DEDUPE
    state["cursor"] = {}
    return True


async def _tick_schema_agent_dedupe(
    state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from collections import defaultdict

    from jvspatial.core import get_default_context

    from jvagent.core.agent import Agent
    from jvagent.core.graph_repair import _compute_reachable_nodes_below

    context = get_default_context()
    cur = state["cursor"]
    if cur.get("dup_groups") is None:
        # Fast-skip: if there is only one Agent no duplicates are possible.
        agent_count = await _entity_count(context, "Agent")
        if agent_count <= 1:
            state["phase"] = PH_SCHEMA_ACTIONS_DEDUPE
            state["cursor"] = {"agent_ids": None, "agent_index": 0}
            return True

        grouped: Dict[str, List[str]] = defaultdict(list)
        for agent in await Agent.find({}):
            ns = getattr(agent, "namespace", "")
            name = getattr(agent, "name", "")
            grouped[f"{ns}\n{name}"].append(agent.id)
        dup_groups = []
        for key, ids in grouped.items():
            if len(ids) > 1:
                dup_groups.append({"key": key, "ids": sorted(ids)})
        cur["dup_groups"] = dup_groups
        cur["group_index"] = 0

    groups: List[Dict[str, Any]] = cur.get("dup_groups", [])
    idx = int(cur.get("group_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    processed = 0
    removed = 0
    dry = state["dry_run"]
    context = get_default_context()

    while idx < len(groups) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        group = groups[idx]
        idx += 1
        processed += 1
        ids = sorted(group.get("ids", []))
        candidates = []
        for candidate_id in ids:
            candidate = await Agent.get(candidate_id)
            if candidate:
                candidates.append(candidate)
        if len(candidates) <= 1:
            continue
        scored: List[Tuple[int, str, Any]] = []
        for candidate in candidates:
            reachable = await _compute_reachable_nodes_below(context, candidate)
            scored.append((len(reachable), candidate.id, candidate))
        scored.sort(key=lambda item: (-item[0], item[1]))
        keep_id = scored[0][1]
        for _, _, dup in scored[1:]:
            if dup.id == keep_id:
                continue
            if not dup:
                continue
            if not dry:
                await dup.delete(cascade=True)
            removed += 1

    cur["group_index"] = idx
    state["result"]["duplicate_agents_removed"] += removed
    if removed:
        clear_cache = getattr(Agent, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()
    if idx >= len(groups):
        state["phase"] = PH_SCHEMA_ACTIONS_DEDUPE
        state["cursor"] = {"agent_ids": None, "agent_index": 0}
    return True


async def _tick_schema_actions_dedupe(
    state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    if cur.get("agent_ids") is None:
        agents = await Agent.find({})
        cur["agent_ids"] = [a.id for a in agents]
        cur["agent_index"] = 0
    agent_ids: List[str] = cur.get("agent_ids", [])
    idx = int(cur.get("agent_index", 0))
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    batch = limits.batch_size
    processed = 0
    removed = 0
    dry = state["dry_run"]

    while idx < len(agent_ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        agent = await Agent.get(agent_ids[idx])
        idx += 1
        processed += 1
        if not agent:
            continue
        actions_nodes = await agent.nodes(node="Actions")
        if len(actions_nodes) <= 1:
            continue
        actions_nodes = sorted(actions_nodes, key=lambda node: node.id)
        keep = actions_nodes[0]
        keep_actions = 0
        keep_nodes = getattr(keep, "nodes", None)
        if callable(keep_nodes):
            keep_result = keep_nodes(direction="out")
            keep_actions = len(
                await keep_result if isawaitable(keep_result) else keep_result
            )
        if keep_actions == 0:
            for candidate in actions_nodes[1:]:
                candidate_actions = 0
                candidate_nodes = getattr(candidate, "nodes", None)
                if callable(candidate_nodes):
                    candidate_result = candidate_nodes(direction="out")
                    candidate_actions = len(
                        await candidate_result
                        if isawaitable(candidate_result)
                        else candidate_result
                    )
                if candidate_actions > keep_actions:
                    keep = candidate
                    keep_actions = candidate_actions
        for dup in actions_nodes:
            if dup.id == keep.id:
                continue
            if not dry:
                await dup.delete(cascade=True)
            removed += 1

    cur["agent_index"] = idx
    state["result"]["duplicate_actions_managers_removed"] += removed
    if idx >= len(agent_ids):
        state["phase"] = PH_SCHEMA_MEMORY_DEDUPE
        state["cursor"] = {"agent_ids": agent_ids, "agent_index": 0}
    return True


async def _tick_schema_memory_dedupe(
    state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import get_default_context

    from jvagent.core.agent import Agent
    from jvagent.core.graph_repair import _compute_reachable_nodes_below
    from jvagent.memory.manager import Memory

    cur = state["cursor"]
    if cur.get("agent_ids") is None:
        agents = await Agent.find({})
        cur["agent_ids"] = [a.id for a in agents]
        cur["agent_index"] = 0
    agent_ids: List[str] = cur.get("agent_ids", [])
    idx = int(cur.get("agent_index", 0))
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    batch = limits.batch_size
    processed = 0
    removed = 0
    dry = state["dry_run"]
    context = get_default_context()

    while idx < len(agent_ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        agent = await Agent.get(agent_ids[idx])
        idx += 1
        processed += 1
        if not agent:
            continue
        memories = await agent.nodes(node="Memory")
        if len(memories) <= 1:
            continue
        memories = sorted(memories, key=lambda node: node.id)
        scored: List[Tuple[int, str, Any]] = []
        for memory in memories:
            reachable = await _compute_reachable_nodes_below(context, memory)
            scored.append((len(reachable), memory.id, memory))
        scored.sort(key=lambda item: (-item[0], item[1]))
        keep_id = scored[0][1]
        for _, _, dup in scored[1:]:
            if dup.id == keep_id:
                continue
            if not dry:
                await dup.delete(cascade=True)
            removed += 1

    cur["agent_index"] = idx
    state["result"]["duplicate_memory_nodes_removed"] += removed
    if removed:
        clear_cache = getattr(Memory, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()
    if idx >= len(agent_ids):
        state["phase"] = PH_SCHEMA_SINGLETON_ACTIONS
        state["cursor"] = {"agent_ids": None, "agent_index": 0}
    return True


async def _tick_schema_singleton_actions(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from collections import defaultdict

    from jvspatial.core.entities.node import Node

    from jvagent.action.base import Action
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    if cur.get("agent_ids") is None:
        agents = await Agent.find({})
        cur["agent_ids"] = [a.id for a in agents]
        cur["agent_index"] = 0
    agent_ids: List[str] = cur.get("agent_ids", [])
    idx = int(cur.get("agent_index", 0))
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    batch = limits.batch_size
    processed = 0
    removed = 0
    dry = state["dry_run"]

    while idx < len(agent_ids) and processed < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        agent_id = agent_ids[idx]
        idx += 1
        processed += 1

        raw = await context.database.find("node", {"context.agent_id": agent_id})
        records = [
            r
            for r in raw
            if r.get("context", {}).get("namespace")
            and r.get("context", {}).get("label")
        ]
        singleton_by_archetype: Dict[str, List[str]] = defaultdict(list)
        for record in records:
            record_ctx = record.get("context", {})
            metadata = record_ctx.get("metadata", {}) or {}
            base_config = metadata.get("config", {}) or {}
            config_overrides = metadata.get("config_overrides", {}) or {}
            merged_config = {**base_config, **config_overrides}
            if merged_config.get("singleton", True) is not True:
                continue
            archetype = metadata.get("class") or record.get("entity", "")
            if not archetype:
                continue
            record_id = record.get("id")
            if record_id:
                singleton_by_archetype[archetype].append(record_id)

        for archetype_ids in singleton_by_archetype.values():
            ordered_ids = sorted(set(archetype_ids))
            for dup_id in ordered_ids[1:]:
                if dry:
                    removed += 1
                    continue
                action = await Action.get(dup_id)
                if action:
                    agent = await Agent.get(agent_id)
                    managers = await agent.nodes(node="Actions") if agent else []
                    action_removed = False
                    for manager in sorted(managers, key=lambda m: m.id):
                        if await manager.deregister_action(dup_id):
                            action_removed = True
                            break
                    if action_removed:
                        removed += 1
                        continue
                ghost_node = await Node.get(dup_id)
                if ghost_node:
                    await ghost_node.delete(cascade=True)
                    removed += 1

    cur["agent_index"] = idx
    state["result"]["duplicate_singleton_actions_removed"] += removed
    if idx >= len(agent_ids):
        state["phase"] = PH_DEAD_EDGES
        state["cursor"] = {"last_edge_id": ""}
    return True


_DEAD_EDGES_CHECKPOINT_EVERY = 32


async def _tick_dead_edges(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Edge, Node

    cur = state["cursor"]
    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    removed = 0
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_SYNC_PREPARE
        state["cursor"] = {
            "last_edge_id": "",
            "acc_node_edges": {},
            "acc_valid_ids": [],
        }
        return True

    processed = 0
    for data in page:
        if limits.max_seconds and time.monotonic() >= deadline:
            break

        source_id = data.get("source", "")
        target_id = data.get("target", "")
        eid = data.get("id", "")

        async def try_delete(edge_data: dict, edge_id: str) -> int:
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
                removed += await try_delete(data, eid)
            else:
                removed += 1
        else:
            source_node = await context.get(Node, source_id)
            target_node = await context.get(Node, target_id)
            if source_node is None or target_node is None:
                if not state["dry_run"]:
                    removed += await try_delete(data, eid)
                else:
                    removed += 1

        if eid:
            cur["last_edge_id"] = eid
        processed += 1
        if processed % _DEAD_EDGES_CHECKPOINT_EVERY == 0 or processed == len(page):
            await repair_checkpoint(state)

    state["result"]["dead_edges_removed"] = (
        state["result"].get("dead_edges_removed", 0) + removed
    )

    full_page = processed == len(page) and len(page) > 0
    if not full_page and processed:
        # Budget ran out mid-page — last_edge_id is the last *processed* edge;
        # next wave continues with ``$gt`` from there.
        await repair_checkpoint(state)
        return True
    if not full_page and not processed:
        # Nothing done this wave (e.g. instant deadline) — keep cursor.
        return True

    if len(page) < batch and full_page:
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
    """Accumulate node->edge ids and valid edge ids from paged edges.

    Uses the repair_scratch collection so the RepairState cursor stays small
    regardless of graph size.
    """
    from jvagent.core.repair_scratch import (
        ensure_scratch_indexes,
        scratch_upsert_bulk,
    )

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    if not run_id:
        import uuid

        run_id = uuid.uuid4().hex
        state["run_id"] = run_id
        cur["run_id"] = run_id
        db = context.database
        await ensure_scratch_indexes(db)

    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    db = context.database

    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_SYNC_APPLY
        state["cursor"] = {"last_node_id": "", "run_id": run_id}
        return True

    node_edge_items: List[Tuple[str, str]] = []
    valid_edge_items: List[Tuple[str, str]] = []
    for data in page:
        eid = data.get("id")
        source = data.get("source")
        target = data.get("target")
        if eid:
            valid_edge_items.append((eid, ""))
        if eid and source:
            # key = "<node_id>|<edge_id>" so we can group by node in apply phase
            node_edge_items.append((f"{source}|{eid}", eid))
        if eid and target:
            node_edge_items.append((f"{target}|{eid}", eid))

    if node_edge_items:
        await scratch_upsert_bulk(db, run_id, "node_edge", node_edge_items)
    if valid_edge_items:
        await scratch_upsert_bulk(db, run_id, "valid_edge", valid_edge_items)

    cur["last_edge_id"] = page[-1].get("id", "")
    cur["run_id"] = run_id
    if len(page) < batch:
        state["phase"] = PH_SYNC_APPLY
        state["cursor"] = {"last_node_id": "", "run_id": run_id}
    return True


async def _tick_sync_apply(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    """Sync node edge_ids reading valid/expected sets from the scratch collection."""
    from jvspatial.core import Node

    from jvagent.core.repair_scratch import scratch_page

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    last = cur.get("last_node_id") or ""
    batch = limits.batch_size
    synced = 0
    db = context.database

    page = await _find_nodes_page(context, last if last else None, batch)
    if not page:
        state["phase"] = PH_ORPHANS_LIST_NODES
        state["cursor"] = {"last_node_id": "", "run_id": run_id}
        return True

    # Build valid_edge set from scratch (page-sized window to keep memory bounded)
    valid_rows = await scratch_page(db, run_id, "valid_edge", None, 50000)
    valid_ids: Set[str] = {r["key"] for r in valid_rows}

    dry = state["dry_run"]
    for data in page:
        node_id = data.get("id")
        if not node_id:
            continue
        current_edge_ids = set(data.get("edges", []))

        # Look up expected edges for this node from scratch
        node_edge_rows = await scratch_page(db, run_id, "node_edge", None, batch)
        expected: Set[str] = set()
        for r in node_edge_rows:
            # key format: "<node_id>|<edge_id>"
            k = r.get("key", "")
            if k.startswith(f"{node_id}|"):
                expected.add(k.split("|", 1)[1])

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
    cur["run_id"] = run_id
    if len(page) < batch:
        state["phase"] = PH_ORPHANS_LIST_NODES
        state["cursor"] = {"last_node_id": "", "run_id": run_id}
    return True


async def _tick_orphans_list_nodes(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    """Enumerate all node IDs into the repair_scratch collection.

    Each node id is stored as a scratch row of kind ``all_node_id`` so the
    RepairState cursor only holds the pagination watermark.
    """
    from jvspatial.core import Root

    from jvagent.core.repair_scratch import ensure_scratch_indexes, scratch_upsert_bulk

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    if not run_id:
        import uuid

        run_id = uuid.uuid4().hex
        state["run_id"] = run_id
        cur["run_id"] = run_id
        await ensure_scratch_indexes(context.database)

    last = cur.get("last_node_id") or ""
    batch = limits.batch_size
    db = context.database

    page = await _find_nodes_page(context, last if last else None, batch)
    if not page:
        root = await Root.get()
        rid = root.id if root else "n.Root.root"
        state["phase"] = PH_ORPHANS_BFS
        state["cursor"] = {"bfs_queue": [rid], "run_id": run_id}
        return True

    items = [(data["id"], "") for data in page if data.get("id")]
    if items:
        await scratch_upsert_bulk(db, run_id, "all_node_id", items)

    cur["last_node_id"] = page[-1].get("id", "")
    cur["run_id"] = run_id
    if len(page) < batch:
        root = await Root.get()
        rid = root.id if root else "n.Root.root"
        state["phase"] = PH_ORPHANS_BFS
        state["cursor"] = {"bfs_queue": [rid], "run_id": run_id}
    return True


async def _tick_orphans_bfs(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    """BFS from root to mark reachable nodes; persists visited set in scratch.

    ``bfs_seen`` is no longer stored in the cursor dict; instead we upsert
    ``bfs_seen`` rows into the scratch collection and only keep the queue
    (a short list of pending node ids) in the cursor.
    """
    from jvspatial.core import Node, Root

    from jvagent.core.repair_scratch import scratch_contains, scratch_upsert_bulk

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    queue = deque(cur.get("bfs_queue", []))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    steps = 0
    db = context.database

    while queue and steps < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        nid = queue.popleft()
        if await scratch_contains(db, run_id, "bfs_seen", nid):
            continue
        await scratch_upsert_bulk(db, run_id, "bfs_seen", [(nid, "")])
        steps += 1
        try:
            node = await context.get(Node, nid)
            if not node:
                continue
            neighbors = await node.nodes(direction="both")
            for nb in neighbors:
                if not await scratch_contains(db, run_id, "bfs_seen", nb.id):
                    queue.append(nb.id)
        except Exception as e:
            logger.debug("BFS error at %s: %s", nid, e)

    cur["bfs_queue"] = list(queue)
    cur["run_id"] = run_id
    if not queue:
        # Compute orphans: all_node_id rows NOT in bfs_seen rows.
        # Both sets live in scratch; we page through all_node_id and check seen.
        from jvagent.core.repair_scratch import scratch_page

        root = await Root.get()
        root_id = root.id if root else "n.Root.root"
        orphan_ids: List[str] = []
        after_key: Optional[str] = None
        while True:
            rows = await scratch_page(db, run_id, "all_node_id", after_key, 500)
            if not rows:
                break
            for r in rows:
                nid = r["key"]
                if nid == root_id:
                    continue
                if not await scratch_contains(db, run_id, "bfs_seen", nid):
                    orphan_ids.append(nid)
            after_key = rows[-1]["key"]
            if len(rows) < 500:
                break

        state["phase"] = PH_ORPHANS_REATTACH
        state["cursor"] = {
            "orphan_ids": orphan_ids,
            "orphan_index": 0,
            "run_id": run_id,
        }
    return True


async def _build_reattach_context() -> Dict[str, Any]:
    """Build lookup maps used by all reattach handlers.

    Fetches Memory and Agent collections once so handlers never need to call
    Memory.find({}) / Agent.find({}) per-orphan (which was O(orphans * memories)).
    """
    from jvagent.core.agent import Agent
    from jvagent.memory.manager import Memory

    memories = await Memory.find({})
    agents = await Agent.find({})
    memory_by_id = {m.id: m for m in memories}
    memory_by_agent_id = {
        getattr(m, "agent_id", ""): m for m in memories if getattr(m, "agent_id", "")
    }
    agents_without_memory = []
    agents_without_actions = []
    for agent in agents:
        mem = await agent.node(node="Memory")
        if not mem:
            agents_without_memory.append(agent)
        actions_mgr = await agent.node(node="Actions")
        if not actions_mgr:
            agents_without_actions.append(agent)

    return {
        "memories": memories,
        "memory_by_id": memory_by_id,
        "memory_by_agent_id": memory_by_agent_id,
        "agents": agents,
        "agents_without_memory": agents_without_memory,
        "agents_without_actions": agents_without_actions,
    }


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

    # Build lookup maps once at the start of this phase (not per-orphan).
    if cur.get("reattach_ctx") is None:
        cur["reattach_ctx"] = await _build_reattach_context()

    reattach_ctx = cur["reattach_ctx"]

    while idx < len(oids):
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        chunk = oids[idx : idx + batch]
        if not chunk:
            break
        n = await _reattach_orphans_chunk(context, chunk, orphan_set, dry, reattach_ctx)
        reattached += n
        idx += len(chunk)

    cur["orphan_index"] = idx
    state["result"]["orphaned_nodes_reattached"] += reattached
    if idx >= len(oids):
        state["phase"] = PH_ORPHANS_INTERACTION
        state["cursor"] = {"orphan_ids": oids, "run_id": cur.get("run_id", "")}
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
    protected_entities = {"Memory", "User", "Conversation", "Interaction"}
    structural_entities = {"App", "Agents", "Agent", "Actions", "Action"}

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
            if not node:
                continue
            entity_name = node.__class__.__name__
            if entity_name in protected_entities:
                incoming_edges = await node.edges(direction="in")
                if incoming_edges:
                    logger.warning(
                        "Skipping orphan delete for %s %s; incoming edges still present",
                        entity_name,
                        node_id,
                    )
                    continue
            if not dry:
                cascade = entity_name in structural_entities
                await node.delete(cascade=cascade)
                deleted += 1
            else:
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
    """Accumulate duplicate edge pairs into scratch rather than cursor."""
    from jvagent.core.repair_scratch import ensure_scratch_indexes, scratch_upsert_bulk

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    if not run_id:
        import uuid

        run_id = uuid.uuid4().hex
        state["run_id"] = run_id
        cur["run_id"] = run_id
        await ensure_scratch_indexes(context.database)

    last = cur.get("last_edge_id") or ""
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    db = context.database

    page = await _find_edges_page(context, last if last else None, batch)
    if not page:
        # Identify keys that have >1 edge: read back from scratch and keep only dups
        from jvagent.core.repair_scratch import scratch_page

        dup_keys: List[str] = []
        after_key: Optional[str] = None
        # Group edge_pair rows by source_target key
        seen_keys: Dict[str, int] = {}
        while True:
            rows = await scratch_page(db, run_id, "edge_pair", after_key, 1000)
            if not rows:
                break
            for r in rows:
                # key = "<source>\n<target>", value = edge_id
                pair_key = r["key"]
                seen_keys[pair_key] = seen_keys.get(pair_key, 0) + 1
            after_key = rows[-1]["key"]
            if len(rows) < 1000:
                break
        dup_keys = sorted(k for k, cnt in seen_keys.items() if cnt > 1)
        state["phase"] = PH_DUP_APPLY
        state["cursor"] = {
            "dup_keys": dup_keys,
            "dup_key_index": 0,
            "run_id": run_id,
        }
        return True

    items: List[Tuple[str, str]] = []
    for data in page:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        source = data.get("source")
        target = data.get("target")
        eid = data.get("id")
        if source and target and eid:
            pair_key = f"{source}\n{target}"
            items.append((f"{pair_key}|{eid}", eid))

    if items:
        await scratch_upsert_bulk(db, run_id, "edge_pair", items)

    cur["last_edge_id"] = page[-1].get("id", "")
    cur["run_id"] = run_id
    if len(page) < batch:
        from jvagent.core.repair_scratch import scratch_page

        seen_keys_final: Dict[str, int] = {}
        after_key_f: Optional[str] = None
        while True:
            rows = await scratch_page(db, run_id, "edge_pair", after_key_f, 1000)
            if not rows:
                break
            for r in rows:
                pair_key = r["key"].rsplit("|", 1)[0]
                seen_keys_final[pair_key] = seen_keys_final.get(pair_key, 0) + 1
            after_key_f = rows[-1]["key"]
            if len(rows) < 1000:
                break
        dup_keys_f = sorted(k for k, cnt in seen_keys_final.items() if cnt > 1)
        state["phase"] = PH_DUP_APPLY
        state["cursor"] = {
            "dup_keys": dup_keys_f,
            "dup_key_index": 0,
            "run_id": run_id,
        }
    return True


async def _tick_dup_apply(
    context: Any, state: Dict[str, Any], limits: RepairLimits
) -> bool:
    from jvspatial.core import Edge, Node

    from jvagent.core.repair_scratch import scratch_page

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
    keys: List[str] = cur.get("dup_keys", [])
    ki = int(cur.get("dup_key_index", 0))
    batch = limits.batch_size
    deadline = time.monotonic() + (limits.max_seconds or 1e9)
    dry = state["dry_run"]
    removed = 0
    processed_keys = 0
    db = context.database

    while ki < len(keys) and processed_keys < batch:
        if limits.max_seconds and time.monotonic() >= deadline:
            break
        key = keys[ki]
        ki += 1
        processed_keys += 1
        prefix = f"{key}|"
        pair_rows = await scratch_page(db, run_id, "edge_pair", prefix[:-1], 200)
        group_ids = sorted(
            {r["value"] for r in pair_rows if r.get("key", "").startswith(prefix)}
        )
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

    cur["dup_key_index"] = ki
    cur["run_id"] = run_id
    state["result"]["duplicate_edges_removed"] += removed
    if ki >= len(keys):
        if not state["dry_run"]:
            state["phase"] = PH_PRUNE_AGENTS
            state["cursor"] = {
                "prune_agent_index": 0,
                "prune_agent_ids": None,
                "run_id": run_id,
            }
        else:
            state["phase"] = PH_DONE
            state["cursor"] = {"run_id": run_id}
    return True


async def _tick_prune_agents(state: Dict[str, Any], limits: RepairLimits) -> bool:
    from jvagent.core.agent import Agent

    cur = state["cursor"]
    run_id: str = cur.get("run_id") or state.get("run_id") or ""
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
        # Drop scratch data for this run now that we're done.
        if run_id:
            try:
                from jvspatial.core import get_default_context

                from jvagent.core.repair_scratch import scratch_drop_run

                await scratch_drop_run(get_default_context().database, run_id)
            except Exception:
                logger.debug(
                    "_tick_prune_agents: scratch_drop_run failed", exc_info=True
                )
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
    if r.get("duplicate_apps_removed"):
        parts.append(f"{r['duplicate_apps_removed']} app node(s) removed")
    if r.get("duplicate_agents_removed"):
        parts.append(f"{r['duplicate_agents_removed']} duplicate agent(s) removed")
    if r.get("duplicate_actions_managers_removed"):
        parts.append(
            f"{r['duplicate_actions_managers_removed']} duplicate actions manager(s) removed"
        )
    if r.get("duplicate_memory_nodes_removed"):
        parts.append(
            f"{r['duplicate_memory_nodes_removed']} duplicate memory node(s) removed"
        )
    if r.get("duplicate_singleton_actions_removed"):
        parts.append(
            f"{r['duplicate_singleton_actions_removed']} duplicate singleton action(s) removed"
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


_PHASE_ORDER: List[str] = [
    PH_MEMORY_COUNTERS,
    PH_MEMORY_AGENTS,
    PH_SCHEMA_APP_DEDUPE,
    PH_SCHEMA_AGENT_DEDUPE,
    PH_SCHEMA_ACTIONS_DEDUPE,
    PH_SCHEMA_MEMORY_DEDUPE,
    PH_SCHEMA_SINGLETON_ACTIONS,
    PH_DEAD_EDGES,
    PH_SYNC_PREPARE,
    PH_SYNC_APPLY,
    PH_ORPHANS_LIST_NODES,
    PH_ORPHANS_BFS,
    PH_ORPHANS_REATTACH,
    PH_ORPHANS_INTERACTION,
    PH_ORPHANS_DELETE,
    PH_DUP_PREPARE,
    PH_DUP_APPLY,
    PH_PRUNE_AGENTS,
    PH_DONE,
]


def _next_phase(current: str) -> Optional[str]:
    """Return the next phase after ``current`` or None if already at DONE."""
    try:
        idx = _PHASE_ORDER.index(current)
        if idx + 1 < len(_PHASE_ORDER):
            return _PHASE_ORDER[idx + 1]
    except ValueError:
        pass
    return None


async def run_repair_session(
    state: Dict[str, Any], limits: RepairLimits
) -> Dict[str, Any]:
    """Run **one** top-level repair phase tick and return.

    This is the synchronous step contract: a single call advances the pipeline
    by at most one tick; :func:`repair_agent_graph` persists state after the
    tick so the next call continues from the same ``phase``/``cursor`` (or
    from a sub-step checkpoint inside a tick).

    Consecutive logical stalls (tick ran but produced no progress) use
    ``state["stall_count"]``; after two stalls the engine force-advances to the
    next phase to avoid a permanent ``in_progress``.
    """
    from jvspatial.core import get_default_context

    context = get_default_context()
    stall_count: int = int(state.get("stall_count", 0))

    phase = state.get("phase", PH_DONE)
    _apply_deferred_phase_skip(state)
    phase = state.get("phase", PH_DONE)
    if phase == PH_DONE:
        state["stall_count"] = stall_count
        return state

    before = json.dumps(state.get("cursor"), sort_keys=True)
    phase_before = state["phase"]

    # --- dispatch (single tick) ---
    tick_coro = None
    if phase == PH_MEMORY_COUNTERS:
        tick_coro = _tick_memory_counters(state, limits)
    elif phase == PH_MEMORY_AGENTS:
        tick_coro = _tick_memory_agents(state, limits)
    elif phase == PH_SCHEMA_APP_DEDUPE:
        tick_coro = _tick_schema_app_dedupe(context, state, limits)
    elif phase == PH_SCHEMA_AGENT_DEDUPE:
        tick_coro = _tick_schema_agent_dedupe(state, limits)
    elif phase == PH_SCHEMA_ACTIONS_DEDUPE:
        tick_coro = _tick_schema_actions_dedupe(state, limits)
    elif phase == PH_SCHEMA_MEMORY_DEDUPE:
        tick_coro = _tick_schema_memory_dedupe(state, limits)
    elif phase == PH_SCHEMA_SINGLETON_ACTIONS:
        tick_coro = _tick_schema_singleton_actions(context, state, limits)
    elif phase == PH_DEAD_EDGES:
        tick_coro = _tick_dead_edges(context, state, limits)
    elif phase == PH_SYNC_PREPARE:
        tick_coro = _tick_sync_prepare(context, state, limits)
    elif phase == PH_SYNC_APPLY:
        tick_coro = _tick_sync_apply(context, state, limits)
    elif phase == PH_ORPHANS_LIST_NODES:
        tick_coro = _tick_orphans_list_nodes(context, state, limits)
    elif phase == PH_ORPHANS_BFS:
        tick_coro = _tick_orphans_bfs(context, state, limits)
    elif phase == PH_ORPHANS_REATTACH:
        tick_coro = _tick_orphans_reattach(context, state, limits)
    elif phase == PH_ORPHANS_INTERACTION:
        tick_coro = _tick_orphans_interaction(context, state, limits)
    elif phase == PH_ORPHANS_DELETE:
        tick_coro = _tick_orphans_delete(context, state, limits)
    elif phase == PH_DUP_PREPARE:
        tick_coro = _tick_dup_prepare(context, state, limits)
    elif phase == PH_DUP_APPLY:
        tick_coro = _tick_dup_apply(context, state, limits)
    elif phase == PH_PRUNE_AGENTS:
        tick_coro = _tick_prune_agents(state, limits)
    else:
        state["stall_count"] = stall_count
        return state

    tick_start = time.monotonic()
    try:
        await tick_coro
    except Exception:
        tick_ms = int((time.monotonic() - tick_start) * 1000)
        logger.error(
            "run_repair_session: tick for phase %s raised unexpectedly (duration_ms=%d)",
            phase,
            tick_ms,
            exc_info=True,
        )
        raise

    await repair_checkpoint(state)

    tick_ms = int((time.monotonic() - tick_start) * 1000)
    logger.info(
        "repair_tick phase=%s status=ok duration_ms=%d",
        phase,
        tick_ms,
    )

    after = json.dumps(state.get("cursor"), sort_keys=True)
    stalled = before == after and phase_before == state["phase"]
    if stalled:
        stall_count += 1
        logger.warning(
            "run_repair_session: stall detected in phase %s (stall_count=%d; cursor_keys=%s)",
            phase,
            stall_count,
            list((state.get("cursor") or {}).keys())[:10],
        )
        if stall_count >= 2:
            next_ph = _next_phase(phase)
            if next_ph:
                logger.warning(
                    "run_repair_session: force-advancing from %s to %s after %d stalls",
                    phase,
                    next_ph,
                    stall_count,
                )
                state["phase"] = next_ph
                state["cursor"] = {}
            stall_count = 0
    else:
        stall_count = 0

    _apply_deferred_phase_skip(state)
    state["stall_count"] = stall_count
    return state
