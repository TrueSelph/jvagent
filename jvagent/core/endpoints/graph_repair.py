"""Admin graph-repair endpoints (run / state / abort)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Query
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response

from jvagent.core.graph_repair import repair_agent_graph


@endpoint(
    "/graph/repair",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "memory_repair_agents": ResponseField(
                field_type=int, description="Agents whose memory was repaired"
            ),
            "orphaned_interactions_deleted": ResponseField(
                field_type=int, description="Orphan interactions deleted"
            ),
            "orphaned_users_reconnected": ResponseField(
                field_type=int, description="Orphan users reconnected"
            ),
            "dual_edges_removed": ResponseField(
                field_type=int, description="Duplicate chain edges removed"
            ),
            "conversation_first_edges_restored": ResponseField(
                field_type=int, description="Conv→first-interaction edges restored"
            ),
            "conversation_branch_edges_removed": ResponseField(
                field_type=int, description="Conv→interaction branch edges removed"
            ),
            "dead_edges_removed": ResponseField(
                field_type=int, description="Dead edges removed"
            ),
            "orphaned_nodes_reattached": ResponseField(
                field_type=int, description="Orphan nodes reattached"
            ),
            "orphaned_nodes_deleted": ResponseField(
                field_type=int, description="Orphan nodes deleted"
            ),
            "node_edge_ids_synced": ResponseField(
                field_type=int, description="Nodes with edge_ids synced"
            ),
            "duplicate_edges_removed": ResponseField(
                field_type=int, description="Duplicate edges removed"
            ),
            "interactions_pruned": ResponseField(
                field_type=int,
                description="Interactions removed by rolling-window prune",
            ),
            "message": ResponseField(field_type=str, description="Success message"),
            "status": ResponseField(
                field_type=str, description="completed or in_progress"
            ),
            "phase": ResponseField(
                field_type=str, description="Current phase when in_progress"
            ),
            "dry_run": ResponseField(
                field_type=Optional[bool],
                description="True when running in dry-run mode",
                default=None,
            ),
            "started_at": ResponseField(
                field_type=Optional[str],
                description="ISO timestamp when session started",
                default=None,
            ),
            "elapsed_seconds": ResponseField(
                field_type=Optional[float],
                description="Wall-clock seconds since started_at",
                default=None,
            ),
        }
    ),
)
async def repair_graph(
    dry_run: bool = Query(
        False, description="If True, report issues without making changes"
    ),
    recent_minutes: Optional[int] = Query(
        None,
        description="Only clean orphan interactions from last N minutes (None = all)",
    ),
    max_seconds: float = Query(
        30.0,
        ge=0.5,
        le=600.0,
        description=(
            "Server-side wall-clock budget for this **single** repair step; a full repair "
            "requires multiple calls until status is completed."
        ),
    ),
) -> Dict[str, Any]:
    """Run one bounded repair step (admin only). Re-invoke until ``status=completed``.

    Concurrency: a distributed DB-level lock (``repair_lock`` collection) ensures
    only one step executes at a time across all replicas. Concurrent callers
    receive ``status=in_progress`` without doing repair work, so spamming this
    endpoint cannot multiply the underlying DB load — but each call still
    issues a paginated find. Operators should treat this as a single-tenant
    admin tool, not a polling target.
    """
    return await repair_agent_graph(
        dry_run=dry_run,
        recent_minutes=recent_minutes,
        max_seconds=max_seconds,
    )


@endpoint(
    "/graph/repair/state",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "status": ResponseField(
                field_type=str, description="no_app, idle, or active"
            ),
            "phase": ResponseField(
                field_type=Optional[str], description="Current phase", default=None
            ),
            "started_at": ResponseField(
                field_type=Optional[str], description="ISO timestamp", default=None
            ),
            "age_seconds": ResponseField(
                field_type=Optional[float],
                description="Seconds since updated_at",
                default=None,
            ),
            "stall_count": ResponseField(
                field_type=Optional[int], description="Stall counter", default=None
            ),
            "run_id": ResponseField(
                field_type=Optional[str],
                description="Scratch / repair run id",
                default=None,
            ),
            "scratch_row_count": ResponseField(
                field_type=Optional[int],
                description="Total scratch rows for run",
                default=None,
            ),
            "version": ResponseField(
                field_type=Optional[str],
                description="RepairState version",
                default=None,
            ),
        }
    ),
)
async def graph_repair_state() -> Dict[str, Any]:
    """Read current ``RepairState`` phase and metadata without modifying anything."""
    from datetime import datetime, timezone

    from jvspatial.core import get_default_context

    from jvagent.core.app import App
    from jvagent.core.repair_scratch import scratch_count
    from jvagent.core.repair_state import RepairState

    app = await App.get()
    if app is None:
        return {"status": "no_app"}

    all_states = await RepairState.find_all(app)
    if not all_states:
        return {"status": "idle"}

    rs = sorted(
        all_states,
        key=lambda s: s.started_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[0]

    now = datetime.now(timezone.utc)
    age = (now - rs.updated_at).total_seconds() if rs.updated_at else None

    db = get_default_context().database
    scratch_rows = 0
    if rs.run_id:
        for kind in ("all_node_id", "bfs_seen", "node_edge", "valid_edge", "edge_pair"):
            scratch_rows += await scratch_count(db, rs.run_id, kind)

    return {
        "status": "active",
        "phase": rs.phase,
        "started_at": rs.started_at.isoformat() if rs.started_at else None,
        "age_seconds": age,
        "stall_count": rs.stall_count,
        "run_id": rs.run_id,
        "scratch_row_count": scratch_rows,
        "version": rs.version,
    }


@endpoint(
    "/graph/repair/abort",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, description="no_app or aborted"),
            "removed": ResponseField(
                field_type=int, description="RepairState records removed"
            ),
            "scratch_runs_dropped": ResponseField(
                field_type=Optional[int],
                description="Scratch runs dropped (when status is aborted)",
                default=None,
            ),
        }
    ),
)
async def graph_repair_abort() -> Dict[str, Any]:
    """Force-evict all ``RepairState`` nodes for this App and drop scratch rows."""
    from jvspatial.core import get_default_context

    from jvagent.core.app import App
    from jvagent.core.repair_scratch import scratch_drop_run
    from jvagent.core.repair_state import RepairState

    app = await App.get()
    if app is None:
        return {"status": "no_app", "removed": 0}

    all_states = await RepairState.find_all(app)
    run_ids = [rs.run_id for rs in all_states if rs.run_id]

    removed = await RepairState.purge_stale(app_id=app.id, ttl_seconds=0)

    db = get_default_context().database
    for run_id in run_ids:
        try:
            await scratch_drop_run(db, run_id)
        except Exception as exc:
            # Scratch drop is best-effort; the repair_state record is already
            # gone, so log the failure but don't surface it to the operator.
            import logging

            logging.getLogger(__name__).warning(
                "graph_repair_abort: scratch_drop_run(%s) failed: %s",
                run_id,
                type(exc).__name__,
            )

    return {
        "status": "aborted",
        "removed": removed,
        "scratch_runs_dropped": len(run_ids),
    }
