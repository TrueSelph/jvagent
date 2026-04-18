"""Agent graph repair entry point.

Public entry: :func:`repair_agent_graph`. The actual work is paginated by
``graph_repair_job`` (which delegates back to a small set of helpers in this
module for traversal and orphan reattachment).
"""

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from jvspatial.core import Node, get_default_context

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
