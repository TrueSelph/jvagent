"""Memory subgraph repair phase ticks."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from jvagent.core.repair_phases.types import (
    PH_MEMORY_AGENTS,
    PH_SCHEMA_APP_DEDUPE,
    RepairLimits,
    repair_checkpoint,
)

logger = logging.getLogger(__name__)

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
