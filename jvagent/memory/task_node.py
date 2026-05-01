"""TaskNode: first-class Node entity for tasks in the spatial graph.

Previously, tasks existed only as raw dicts in ``Conversation.active_tasks``.
Promoting them to Node entities gives tasks proper lifecycle hooks, indexed
queries, cascade-delete behavior, and graph-native traversal.

The ``active_tasks`` list on Conversation remains as a denormalized cache
for fast context-window inclusion. TaskService dual-writes to both the
TaskNode and the cache.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ACTIVE_STATUSES = frozenset({"active", "pending", "triggered", "reserved"})
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "max_iterations",
        "superseded",
        "stuck_forced",
    }
)


@compound_index(
    [("conversation_id", 1), ("status", 1)],
    name="task_conversation_status",
)
@compound_index(
    [("agent_id", 1), ("status", 1), ("next_trigger_at", 1)],
    name="task_agent_trigger",
)
class TaskNode(Node):
    """First-class task entity in the spatial graph.

    Connected to a Conversation via an outgoing edge from Conversation.
    Indexed for efficient queries by conversation, agent, status, and
    trigger time.
    """

    # Identity
    conversation_id: str = attribute(
        indexed=True,
        description="ID of the Conversation this task belongs to",
    )
    agent_id: str = attribute(
        indexed=True,
        description="ID of the Agent this task belongs to",
    )
    task_id: str = attribute(
        indexed=True,
        description="Business task ID (not the Node ID)",
    )

    # Classification
    task_type: str = attribute(
        indexed=True,
        description="Logical category (PROACTIVE, AGENTIC_LOOP, etc.)",
    )
    description: str = attribute(description="Human-readable task description")
    action_name: Optional[str] = attribute(
        indexed=True,
        default=None,
        description="Name of the Action that owns this task",
    )

    # Lifecycle
    status: str = attribute(
        indexed=True,
        default="active",
        description="Current status (active, completed, failed, etc.)",
    )
    created_at: str = attribute(default_factory=_now_iso)
    updated_at: str = attribute(default_factory=_now_iso)
    last_heartbeat_at: str = attribute(default_factory=_now_iso)
    terminal_at: Optional[str] = attribute(default=None)

    # Scheduling
    next_trigger_at: Optional[str] = attribute(
        indexed=True,
        default=None,
        description="ISO-8601 timestamp for scheduled trigger",
    )
    trigger_condition: Optional[str] = attribute(
        default=None, description="Keyword/phrase/mood trigger condition"
    )

    # Data
    objective: Optional[str] = attribute(
        default=None, description="Free-text objective set by SkillAction"
    )
    metadata: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Flexible bag: steps, outputs, task_plan, etc.",
    )
    failure_cause: Optional[str] = attribute(default=None)
    retry_count: int = attribute(default=0)
    provenance_refs: List[str] = attribute(default_factory=list)

    # =========================================================================
    # Lifecycle helpers
    # =========================================================================

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def heartbeat(self) -> None:
        now = _now_iso()
        self.updated_at = now
        self.last_heartbeat_at = now

    # =========================================================================
    # Serialisation
    # =========================================================================

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Return a dict matching the ``active_tasks`` list format."""
        meta = dict(self.metadata)
        if self.failure_cause:
            meta.setdefault("failure_reason", self.failure_cause)
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "action_name": self.action_name,
            "status": self.status,
            "next_trigger_at": self.next_trigger_at,
            "trigger_condition": self.trigger_condition,
            "metadata": meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "terminal_at": self.terminal_at,
            "_schema_version": 1,
        }

    @classmethod
    def from_legacy_dict(
        cls,
        data: Dict[str, Any],
        *,
        conversation_id: str,
        agent_id: str,
    ) -> "TaskNode":
        """Build a TaskNode from a legacy active_tasks dict."""
        meta = dict(data.get("metadata") or {})
        return cls(
            conversation_id=conversation_id,
            agent_id=agent_id,
            task_id=str(data.get("task_id", uuid.uuid4().hex)),
            task_type=str(data.get("task_type", "")),
            description=str(data.get("description", "")),
            action_name=data.get("action_name"),
            status=str(data.get("status", "active")),
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            last_heartbeat_at=str(data.get("last_heartbeat_at", _now_iso())),
            terminal_at=data.get("terminal_at"),
            next_trigger_at=data.get("next_trigger_at"),
            trigger_condition=data.get("trigger_condition"),
            objective=meta.pop("objective", None),
            metadata=meta,
            failure_cause=meta.pop("failure_reason", None),
            retry_count=int(meta.pop("retry_count", 0)),
            provenance_refs=list(meta.pop("provenance_refs", [])),
        )

    # =========================================================================
    # Queries
    # =========================================================================

    @classmethod
    async def find_by_conversation(
        cls,
        conversation_id: str,
        *,
        status: Optional[str] = None,
    ) -> List["TaskNode"]:
        """Find tasks for a conversation, optionally filtered by status."""
        query: Dict[str, Any] = {"conversation_id": conversation_id}
        if status:
            query["status"] = status
        return await cls.find(query)  # type: ignore[return-value]

    @classmethod
    async def find_scheduled_triggers(
        cls,
        *,
        before: datetime,
        limit: int = 100,
    ) -> List["TaskNode"]:
        """Find active tasks whose trigger time has elapsed."""
        from jvspatial.core.context import get_default_context

        ctx = get_default_context()
        type_code = ctx._get_entity_type_code(cls)
        collection = ctx._get_collection_name(type_code)
        raw = await ctx.database.find(
            collection,
            {
                "status": "active",
                "next_trigger_at": {"$lte": before.isoformat()},
            },
            limit=limit,
        )
        return [cls._from_raw(r) for r in raw]

    @classmethod
    async def find_stale(
        cls,
        *,
        ttl_seconds: int = 3600,
        limit: int = 100,
    ) -> List["TaskNode"]:
        """Find active tasks that haven't heartbeat'd within the TTL."""
        cutoff = _now_iso()
        from jvspatial.core.context import get_default_context

        ctx = get_default_context()
        type_code = ctx._get_entity_type_code(cls)
        collection = ctx._get_collection_name(type_code)
        raw = await ctx.database.find(
            collection,
            {
                "status": {"$in": list(ACTIVE_STATUSES)},
                "last_heartbeat_at": {"$lt": cutoff},
            },
            limit=limit,
        )
        return [cls._from_raw(r) for r in raw]
