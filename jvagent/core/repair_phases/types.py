"""Shared types and phase identifiers for graph repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

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
