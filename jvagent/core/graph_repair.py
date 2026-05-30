"""Agent graph repair entry point.

Public entry: :func:`repair_agent_graph`. The actual work is paginated by
``graph_repair_job`` (which delegates back to a small set of helpers in this
module for traversal and orphan reattachment).
"""

import asyncio
import logging
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from jvspatial.core import Node, get_default_context
from jvspatial.db.work_claim import claim_record, release_claim

logger = logging.getLogger(__name__)

_REPAIR_LOCK_COLLECTION = "repair_lock"
# Grace period added on top of max_seconds so that a Lambda invocation that is
# forcibly terminated (OOM, timeout) has its lock expire soon after its budget.
_REPAIR_LOCK_GRACE_SECONDS = 60


@asynccontextmanager
async def _distributed_repair_lock(
    app_id: str,
    *,
    max_seconds: float = 30.0,
) -> AsyncGenerator[bool, None]:
    """Acquire a distributed repair lock for the given App.

    Yields True when the lock was acquired, False when another worker holds it.
    The lock is stored as a document in ``repair_lock`` and is DB-level atomic
    on MongoDB (``find_one_and_update``); JSON/SQLite fall back to best-effort.

    ``stale_seconds`` is derived from ``max_seconds`` so a Lambda invocation
    that is forcibly terminated never blocks other workers for more than
    ``max_seconds + _REPAIR_LOCK_GRACE_SECONDS`` seconds.
    """
    from jvspatial.core import get_default_context

    db = get_default_context().database
    record_id = f"graph_repair:{app_id}"
    stale_seconds = max_seconds + _REPAIR_LOCK_GRACE_SECONDS

    # Atomically create the lock document if it does not exist.
    # Using find_one_and_update with upsert=True avoids the TOCTOU race that
    # occurs when two concurrent Lambda invocations both find no document and
    # both call db.save — the second save would silently wipe the first.
    try:
        if hasattr(db, "find_one_and_update"):
            await db.find_one_and_update(
                _REPAIR_LOCK_COLLECTION,
                {"_id": record_id},
                {"$setOnInsert": {"id": record_id, "_id": record_id}},
                upsert=True,
            )
        else:
            # Fallback for non-Mongo backends (SQLite / JsonDB).
            existing = await db.get(_REPAIR_LOCK_COLLECTION, record_id)
            if existing is None:
                await db.save(
                    _REPAIR_LOCK_COLLECTION, {"id": record_id, "_id": record_id}
                )
    except Exception as exc:
        # Seeding the lock row is best-effort — claim_record below still gates
        # the repair — but a persistent failure here is worth surfacing.
        logger.debug("graph_repair: lock-row seed failed: %s", exc)

    doc, token = await claim_record(
        db,
        _REPAIR_LOCK_COLLECTION,
        record_id,
        stale_seconds=stale_seconds,
    )
    acquired = token is not None
    try:
        yield acquired
    finally:
        if acquired and token:
            await release_claim(db, _REPAIR_LOCK_COLLECTION, record_id, token)


async def repair_agent_graph(
    dry_run: bool = False,
    recent_minutes: Optional[int] = None,
    *,
    max_seconds: float = 30.0,
    batch_size: int = 500,
) -> Dict[str, Any]:
    """Run graph repair with a synchronous one-tick / one-step contract.

    When an ``App`` exists, each call runs **one** top-level phase tick, then
    persists to ``RepairState``. Re-invoke (or schedule) until ``status`` is
    ``completed``; a crash or timeout can resume on the next call from
    ``phase``/``cursor`` (or from in-tick ``_checkpoint`` data).

    When there is **no** ``App`` node, progress cannot be stored; the in-memory
    session runs to completion in a single call (safety-capped) so dev/tests
    without a bootstrapped app still get a full pass.
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

    # GC stray RepairState nodes via entity type-scan before acquiring the lock.
    # This catches detached nodes that find_all(app) cannot see.
    if app is not None:
        try:
            purged = await RepairState.purge_stale(app_id=app.id)
            if purged:
                logger.info(
                    "repair_agent_graph: purged %d stale RepairState(s)", purged
                )
        except Exception:
            logger.warning("repair_agent_graph: purge_stale failed", exc_info=True)

    if app is None:
        # No App node: RepairState cannot be attached, so we cannot resume across
        # HTTP calls. Run ticks in-process until the pipeline finishes (or a safety
        # cap), matching tests and minimal graphs without a bootstrapped app.
        state = _initial_session_state(dry_run, recent_minutes)
        _no_app_max_steps = 200_000
        for _step in range(_no_app_max_steps):
            if state.get("phase") == PH_DONE:
                break
            state = await run_repair_session(state, limits)
        else:
            logger.error(
                "repair_agent_graph: no-App session exceeded %d step(s); phase=%s",
                _no_app_max_steps,
                state.get("phase"),
            )
        return _build_http_response(state, _build_message, PH_DONE, started_at)

    # Use a distributed lock so multiple FastAPI workers cannot race to create
    # duplicate RepairState nodes.  The in-process asyncio.Lock (_get_lock)
    # is kept as a secondary guard within the same process and is created
    # lazily so it is always bound to the current running event loop
    # (important for Lambda warm-container reuse with framework event loops).
    async with RepairState._get_lock():
        async with _distributed_repair_lock(
            app.id, max_seconds=max_seconds
        ) as acquired:
            if not acquired:
                # Another worker is actively repairing; return current status.
                repair_state = await RepairState.current(app)
                if repair_state:
                    phase = repair_state.phase
                    result = dict(repair_state.result or {})
                    result["message"] = "Another worker is running repair"
                    result["status"] = "in_progress"
                    result["phase"] = phase
                    rs_started = repair_state.started_at
                    result["started_at"] = (
                        rs_started.isoformat() if rs_started else started_at.isoformat()
                    )
                    # AUDIT-core H-7: report the actual elapsed time of the
                    # in-flight repair (caller previously got 0.0, which
                    # made observability misread "no progress" for every
                    # contended request).
                    try:
                        anchor = rs_started or started_at
                        if anchor.tzinfo is None:
                            anchor = anchor.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        result["elapsed_seconds"] = max(
                            0.0, (now - anchor).total_seconds()
                        )
                    except Exception:
                        result["elapsed_seconds"] = 0.0
                    return result
                # No state found; safe to proceed (lock may have just expired).

            # Drop duplicate / orphan RepairState rows (e.g. tests, crashed links)
            # before re-loading progress so a stray node cannot hide the live one.
            try:
                n_cons = await RepairState.consolidate_for_app(app)
                if n_cons:
                    logger.info(
                        "repair_agent_graph: consolidated %d stray RepairState(s)",
                        n_cons,
                    )
            except Exception:
                logger.warning(
                    "repair_agent_graph: consolidate_for_app failed", exc_info=True
                )

            # Collect ALL RepairState nodes — previous crashed runs may have left
            # more than one attached to App.
            all_states = await RepairState.find_all(app)

            if len(all_states) > 1:
                # Keep the most recently *updated* row (it holds the last checkpoint).
                all_states.sort(
                    key=lambda s: (
                        s.updated_at or datetime.min.replace(tzinfo=timezone.utc),
                        s.started_at or datetime.min.replace(tzinfo=timezone.utc),
                    )
                )
                for stale in all_states[:-1]:
                    logger.warning(
                        "repair_agent_graph: removing stale RepairState %s", stale.id
                    )
                    try:
                        await stale.finish()
                    except Exception:
                        logger.warning(
                            "repair_agent_graph: could not remove stale RepairState %s",
                            stale.id,
                            exc_info=True,
                        )
                repair_state: Optional[RepairState] = all_states[-1]
            elif all_states:
                repair_state = all_states[0]
            else:
                repair_state = None

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
                # Persist the run_id immediately so GC and abort can find scratch rows.
                repair_state.run_id = state.get("run_id", "")
                await repair_state.save()
            else:
                # Drop any cached instance so the resumed cursor/phase are read
                # from the database after a previous wave (context cache can be stale).
                ctx = get_default_context()
                await ctx._remove_from_cache(repair_state.id)
                reloaded = await ctx.get(RepairState, repair_state.id)
                if reloaded is not None:
                    repair_state = reloaded
                state = state_from_dict(
                    {
                        "v": repair_state.version,
                        "phase": repair_state.phase,
                        "cursor": repair_state.cursor,
                        "result": repair_state.result,
                        "dry_run": repair_state.dry_run,
                        "recent_minutes": repair_state.recent_minutes,
                        "run_id": repair_state.run_id,
                        "stall_count": repair_state.stall_count,
                    },
                    dry_run=dry_run,
                    recent_minutes=recent_minutes,
                )
            started_at = repair_state.started_at or started_at

            async def _persist_repair_state(s: dict) -> None:
                if s.get("phase") == PH_DONE:
                    return
                p = state_to_dict(s)
                await repair_state.save_progress(
                    phase=p["phase"],
                    cursor=p["cursor"],
                    result=p["result"],
                    run_id=p.get("run_id", ""),
                    stall_count=s.get("stall_count", 0),
                )

            state["_checkpoint"] = _persist_repair_state
            try:
                # Stall / force-advance is meant for multiple ticks in one
                # ``run_repair_session`` (e.g. no-App in-process loop). One tick per
                # HTTP call must not combine with a previous call's stall count or
                # we skip a phase after two slow-but-legit single-tick steps.
                state["stall_count"] = 0
                state = await run_repair_session(state, limits)
            except asyncio.CancelledError:
                if state.get("phase") != PH_DONE:
                    try:
                        await _persist_repair_state(state)
                    except Exception:
                        logger.debug(
                            "repair_agent_graph: best-effort checkpoint on cancel failed",
                            exc_info=True,
                        )
                raise
            except Exception:
                logger.error(
                    "repair_agent_graph: session raised unexpectedly; cleaning up RepairState %s",
                    repair_state.id,
                    exc_info=True,
                )
                try:
                    await repair_state.finish()
                except Exception:
                    logger.warning(
                        "repair_agent_graph: could not clean up RepairState %s after session error",
                        repair_state.id,
                        exc_info=True,
                    )
                raise
            else:
                payload = state_to_dict(state)

                if state.get("phase") == PH_DONE:
                    await repair_state.finish()
                else:
                    await repair_state.save_progress(
                        phase=payload["phase"],
                        cursor=payload["cursor"],
                        result=payload["result"],
                        run_id=payload.get("run_id", ""),
                        stall_count=state.get("stall_count", 0),
                    )
            finally:
                state.pop("_checkpoint", None)

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
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> int:
    """Reattach handlers for a subset of orphan node ids (batched repair).

    ``reattach_ctx`` is a pre-built lookup dict produced by
    ``_build_reattach_context`` in the job module.  Passing it avoids per-orphan
    ``Memory.find({})`` and ``Agent.find({})`` calls in the handlers.
    """
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
                if await handler(context, node, orphan_ids, dry_run, reattach_ctx):
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
