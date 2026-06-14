"""Persistent app-level graph repair progress state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute, compound_index

logger = logging.getLogger(__name__)


@compound_index(
    [("app_id", 1), ("updated_at", 1)],
    name="repair_state_app_updated",
)
class RepairState(Node):
    """Temporary state for app-wide graph repair progress."""

    phase: str = attribute(default="done", description="Current graph repair phase")
    cursor: Dict[str, Any] = attribute(
        default_factory=dict, description="Opaque phase cursor data"
    )
    result: Dict[str, Any] = attribute(
        default_factory=dict, description="Aggregated repair counters"
    )
    dry_run: bool = attribute(default=False, description="Whether repair is dry-run")
    recent_minutes: Optional[int] = attribute(
        default=None,
        description="Recent window filter passed to memory repair",
    )
    version: int = attribute(default=1, description="Repair state schema version")
    started_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this repair session started",
    )
    updated_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this repair session was last updated",
    )
    app_id: str = attribute(
        default="",
        description="ID of the App node this repair belongs to (for GC by type scan)",
    )
    stall_count: int = attribute(
        default=0,
        description="Number of consecutive stalled ticks in the current phase",
    )
    run_id: str = attribute(
        default="",
        description="Unique identifier for this repair run (links to repair_scratch rows)",
    )

    # Lazily created so the Lock is always bound to the *running* event loop.
    # A class-level Lock created at import time would be tied to whatever loop
    # was active when the module was first imported — in Lambda this can differ
    # from the loop that runs handler coroutines, causing "attached to a
    # different loop" errors on warm containers with some frameworks.
    _lock: ClassVar[Optional[asyncio.Lock]] = None
    _lock_loop: ClassVar[Optional[Any]] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Return the process-local asyncio.Lock, recreating it if the event loop changed."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if cls._lock is None or cls._lock_loop is not loop:
            cls._lock = asyncio.Lock()
            cls._lock_loop = loop
        return cls._lock

    @classmethod
    async def current(cls, app: Any) -> Optional["RepairState"]:
        """Return the current RepairState node connected to app, if present.

        Prefer :meth:`find_all` when you need to detect and purge duplicates.
        """
        if app is None:
            return None
        for node in await app.nodes():
            if isinstance(node, cls):
                return node
        return None

    @classmethod
    async def find_all(cls, app: Any) -> List["RepairState"]:
        """Return ALL RepairState nodes connected to app.

        Unlike :meth:`current`, this returns every instance so callers can
        detect and clean up duplicate nodes left by previous crashed runs.
        """
        if app is None:
            return []
        return [node for node in await app.nodes() if isinstance(node, cls)]

    @classmethod
    async def purge_stale(cls, *, app_id: str, ttl_seconds: int = 3600) -> int:
        """Delete RepairState nodes that are either disconnected from App or too old.

        Uses a DB-level type-scan (``RepairState.find``) so it catches nodes whose
        edge to App was never written or was torn, which ``find_all`` cannot see.

        Args:
            app_id: ID of the App node (used to narrow the query when app_id is set).
            ttl_seconds: Grace period in seconds; nodes last updated before this
                cutoff are treated as stale.  Pass 0 to force-evict all states for
                this App (useful for the /graph/repair/abort endpoint).

        Returns:
            Number of RepairState nodes removed.
        """
        from jvspatial.core import get_default_context

        context = get_default_context()
        removed = 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)

        try:
            # Broad scan: all RepairState nodes with a matching app_id.
            query: Dict[str, Any] = {}
            if app_id:
                query["context.app_id"] = app_id
            candidates: List["RepairState"] = await cls.find(query)
        except Exception:
            logger.warning("purge_stale: find failed", exc_info=True)
            return 0

        for rs in candidates:
            try:
                stale_age = rs.updated_at is None or rs.updated_at < cutoff
                # Check whether the node is actually reachable from App.
                connected = False
                if app_id:
                    # Use the base Node.get so we are not constrained to App's
                    # singleton get() which ignores arguments.
                    app_node = await context.get(Node, app_id)
                    if app_node and await app_node.is_connected_to(rs):
                        connected = True
                else:
                    connected = True  # no app_id: be conservative

                if stale_age or not connected:
                    logger.warning(
                        "purge_stale: removing RepairState %s (stale_age=%s, connected=%s)",
                        rs.id,
                        stale_age,
                        connected,
                    )
                    await rs.finish()
                    removed += 1
            except Exception:
                logger.warning(
                    "purge_stale: could not remove RepairState %s", rs.id, exc_info=True
                )

        return removed

    @classmethod
    async def consolidate_for_app(cls, app: Any) -> int:
        """Remove duplicate and orphan ``RepairState`` nodes for *app*.

        Scans all ``RepairState`` records whose ``app_id`` matches *app*'s id
        (see :meth:`find` on the entity).  For each such node:

        * If *app* is not directly connected to it, the state is a stray from a
          crashed or partial write — it is finished via :meth:`finish`.
        * If more than one state is connected to *app*, all but the one with
          the most recent ``updated_at`` (tie-break ``started_at``) are
          removed.

        This complements :meth:`purge_stale` (which uses a TTL) so tests and
        short-lived runs that leave **live** but duplicate or disconnected rows
        are cleaned the next time repair starts.

        Returns:
            Number of ``RepairState`` nodes removed.
        """
        from jvspatial.core import get_default_context

        if app is None:
            return 0
        app_id = getattr(app, "id", None) or ""
        if not app_id:
            return 0
        context = get_default_context()
        removed = 0
        min_dt = datetime.min.replace(tzinfo=timezone.utc)

        def _ts(s: "RepairState") -> tuple:
            ua = s.updated_at or min_dt
            sa = s.started_at or min_dt
            return (ua, sa, s.id or "")

        try:
            candidates: List[RepairState] = await cls.find({"context.app_id": app_id})
        except Exception:
            logger.warning("consolidate_for_app: find by app_id failed", exc_info=True)
            candidates = []

        if not candidates:
            # Fallback: full scan (small collections / tests) when app_id index misses.
            try:
                broad: List[RepairState] = await cls.find({})
            except Exception:
                logger.warning("consolidate_for_app: broad find failed", exc_info=True)
                return 0
            candidates = [c for c in broad if getattr(c, "app_id", None) == app_id]

        if not candidates:
            return 0

        # Resolve app in case a stale *app* reference was passed in.
        app_node = app
        try:
            fresh = await context.get(Node, app_id)
            if fresh is not None:
                app_node = fresh
        except Exception:
            app_node = app

        connected: List[RepairState] = []
        for rs in candidates:
            try:
                connected_to_app = await app_node.is_connected_to(rs)
            except Exception:
                logger.warning(
                    "consolidate_for_app: could not check App↔RepairState edge for %s; "
                    "skipping (not removing)",
                    rs.id,
                    exc_info=True,
                )
                continue
            if connected_to_app:
                connected.append(rs)
            else:
                logger.warning(
                    "consolidate_for_app: removing orphan RepairState %s (no App edge)",
                    rs.id,
                )
                try:
                    await rs.finish()
                    removed += 1
                except Exception:
                    logger.warning(
                        "consolidate_for_app: could not remove orphan %s", rs.id
                    )

        if len(connected) <= 1:
            return removed

        connected.sort(key=_ts)
        # Keep the most recently updated; drop older duplicates.
        for stale in connected[:-1]:
            logger.warning(
                "consolidate_for_app: removing duplicate RepairState %s (keep %s)",
                stale.id,
                connected[-1].id,
            )
            try:
                await stale.finish()
                removed += 1
            except Exception:
                logger.warning(
                    "consolidate_for_app: could not remove duplicate %s", stale.id
                )
        return removed

    @classmethod
    async def begin(
        cls,
        app: Any,
        *,
        dry_run: bool,
        recent_minutes: Optional[int],
        version: int,
    ) -> "RepairState":
        """Create and connect a fresh RepairState to app."""
        from jvagent.core.graph_repair_job import (
            PH_MEMORY_COUNTERS,
            PH_SCHEMA_APP_DEDUPE,
        )

        # Must not use the string ``"done"`` — that equals :data:`PH_DONE` in
        # ``graph_repair_job`` and makes a session look *complete* before the
        # first :meth:`save_progress` (e.g. client timeout), so resume would skip
        # all work or delete the node in the success path.
        initial_phase = PH_SCHEMA_APP_DEDUPE if dry_run else PH_MEMORY_COUNTERS
        now = datetime.now(timezone.utc)
        app_id_val = getattr(app, "id", "") if app is not None else ""
        state = cls(
            phase=initial_phase,
            cursor={},
            result={},
            dry_run=dry_run,
            recent_minutes=recent_minutes,
            version=version,
            started_at=now,
            updated_at=now,
            app_id=app_id_val,
        )
        await state.save()
        await app.connect(state, direction="out")
        return state

    async def save_progress(
        self,
        *,
        phase: str,
        cursor: Dict[str, Any],
        result: Dict[str, Any],
        run_id: str = "",
        stall_count: int = 0,
    ) -> None:
        """Persist state progress for a bounded repair call."""
        self.phase = phase
        self.cursor = cursor
        self.result = result
        self.updated_at = datetime.now(timezone.utc)
        if run_id:
            self.run_id = run_id
        self.stall_count = stall_count
        await self.save()

    async def finish(self) -> None:
        """Delete state node and its edges when repair is complete/reset.

        After deleting each edge this method also calls
        ``context.atomic_remove_edge_id`` on the *other* endpoint (typically
        the App node) so that its stored ``edge_ids`` attribute is kept
        consistent.  Without this, the sync phase of the next repair run would
        always detect a stale edge reference on App and report
        ``node_edge_ids_synced: 1``, causing an apparent never-ending repair
        loop even on a healthy graph.

        This method is hardened to always remove the node document even when
        edge cleanup or the high-level ``delete()`` raises.  A raw DB delete is
        used as the last-resort fallback so no RepairState ever survives.
        """
        context = await self.get_context()
        try:
            for edge in await self.edges(direction="both"):
                if not isinstance(edge, Edge):
                    continue
                # Determine the other endpoint of this edge (not self)
                other_id = edge.target if edge.source == self.id else edge.source
                if other_id:
                    try:
                        await context.atomic_remove_edge_id(other_id, edge.id)
                    except Exception:
                        logger.warning(
                            "repair_state.finish: could not remove edge_id %s from node %s",
                            edge.id,
                            other_id,
                        )
                try:
                    await context.delete(edge, cascade=False)
                except Exception:
                    logger.warning(
                        "repair_state.finish: could not delete edge %s", edge.id
                    )
        except Exception:
            logger.warning(
                "repair_state.finish: edge cleanup failed for %s",
                self.id,
                exc_info=True,
            )

        try:
            await self.delete(cascade=False)
        except Exception:
            logger.warning(
                "repair_state.finish: high-level delete failed for %s; falling back to raw DB delete",
                self.id,
            )
            try:
                await context.database.delete("node", self.id)
            except Exception:
                logger.error(
                    "repair_state.finish: raw DB delete also failed for %s",
                    self.id,
                    exc_info=True,
                )
