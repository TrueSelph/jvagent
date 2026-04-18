"""Persistent app-level graph repair progress state."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute


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
        """Return the current RepairState node connected to app, if present."""
        if app is None:
            return None
        for node in await app.nodes():
            if isinstance(node, cls):
                return node
        return None

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
        """Delete state node and its edges when repair is complete/reset."""
        context = await self.get_context()
        for edge in await self.edges(direction="both"):
            if isinstance(edge, Edge):
                await context.delete(edge, cascade=False)
        await self.delete(cascade=False)
