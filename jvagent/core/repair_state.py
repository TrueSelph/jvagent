"""Persistent app-level graph repair progress state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


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

    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

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
    async def begin(
        cls,
        app: Any,
        *,
        dry_run: bool,
        recent_minutes: Optional[int],
        version: int,
    ) -> "RepairState":
        """Create and connect a fresh RepairState to app."""
        now = datetime.now(timezone.utc)
        state = cls(
            phase="done",
            cursor={},
            result={},
            dry_run=dry_run,
            recent_minutes=recent_minutes,
            version=version,
            started_at=now,
            updated_at=now,
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
    ) -> None:
        """Persist state progress for a bounded repair call."""
        self.phase = phase
        self.cursor = cursor
        self.result = result
        self.updated_at = datetime.now(timezone.utc)
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
        """
        context = await self.get_context()
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
            await context.delete(edge, cascade=False)
        await self.delete(cascade=False)
