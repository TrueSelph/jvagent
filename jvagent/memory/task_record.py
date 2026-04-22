"""TaskRecord: versioned, typed task schema with validated lifecycle transitions.

Replaces ad-hoc dict manipulation in TaskService with a first-class schema
that enforces state-machine semantics.  All TaskService methods now operate on
TaskRecord objects internally; legacy dict views are derived on the way out so
existing API clients continue to work unchanged.

State machine
-------------

    created ──► active ──► reserved ──► completed
                │                    └─► failed
                │                    └─► cancelled
                │                    └─► timed_out
                │                    └─► max_iterations
                │                    └─► superseded
                └──────────────────────────────────► (any terminal directly)

Transitions FROM a terminal state are not allowed (idempotent by default).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------
# Status constants (mirrors TaskService sets)
# ------------------------------------------------------------------

ACTIVE_STATUSES = frozenset({"active", "pending", "triggered", "reserved"})
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "max_iterations",
        "superseded",
    }
)
ALLOWED_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES

# Legal transitions: maps source_status → set of allowed target statuses.
# Any terminal status may be reached from any non-terminal status.
_VALID_TRANSITIONS: Dict[str, frozenset] = {
    "active": frozenset(TERMINAL_STATUSES | {"reserved", "pending", "triggered"}),
    "pending": frozenset(TERMINAL_STATUSES | {"active", "triggered"}),
    "triggered": frozenset(TERMINAL_STATUSES | {"active", "reserved"}),
    "reserved": frozenset(TERMINAL_STATUSES | {"active"}),
    # Terminal statuses → empty (no further transitions)
    **{s: frozenset() for s in TERMINAL_STATUSES},
}

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvalidTaskTransition(ValueError):
    """Raised when a status transition violates the state machine."""


# ------------------------------------------------------------------
# StepRecord
# ------------------------------------------------------------------


@dataclass
class StepRecord:
    """One step event within a task run.

    Attributes:
        step_type: Logical step category (e.g. ``thinking``, ``tool_call``).
        iteration: Loop iteration number.
        timestamp: ISO-8601 UTC.
        details: Arbitrary extra data.
    """

    step_type: str
    iteration: int
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepRecord":
        return cls(
            step_type=str(data.get("type") or data.get("step_type", "")),
            iteration=int(data.get("iteration", 0)),
            timestamp=str(data.get("timestamp", _now_iso())),
            details={
                k: v
                for k, v in data.items()
                if k not in ("type", "step_type", "iteration", "timestamp")
            },
        )


# ------------------------------------------------------------------
# TaskRecord
# ------------------------------------------------------------------


@dataclass
class TaskRecord:
    """Versioned, typed task record.

    Attributes:
        task_id: Unique identifier.
        task_type: Logical category (e.g. ``AGENTIC_LOOP``).
        description: Human-readable task description.
        action_name: Name of the Action that owns this task.
        status: Current status string (validated on set via ``transition``).
        schema_version: Schema version for forward-compatibility.
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-updated timestamp.
        last_heartbeat_at: ISO-8601 last heartbeat timestamp.
        terminal_at: ISO-8601 terminal timestamp (None while active).
        next_trigger_at: Optional ISO-8601 for scheduled triggers.
        trigger_condition: Optional trigger condition expression.
        objective: Free-text objective (set by SkillAction, optional).
        steps: Ordered list of StepRecord events.
        outputs: Key-value outputs produced by the task.
        failure_cause: Error string populated on failure.
        retry_count: How many times the task was retried.
        provenance_refs: Evidence log entry IDs linked to this task.
        metadata: Flexible key-value bag (for backward compatibility).
    """

    task_id: str
    task_type: str
    description: str
    action_name: Optional[str]
    status: str
    schema_version: int = SCHEMA_VERSION

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_heartbeat_at: str = field(default_factory=_now_iso)
    terminal_at: Optional[str] = None

    next_trigger_at: Optional[str] = None
    trigger_condition: Optional[str] = None

    objective: Optional[str] = None
    steps: List[StepRecord] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    failure_cause: Optional[str] = None
    retry_count: int = 0
    provenance_refs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def transition(self, new_status: str, *, allow_noop: bool = True) -> None:
        """Apply a validated status transition.

        Args:
            new_status: Target status string.
            allow_noop: If True, transitioning to the current status is a no-op.

        Raises:
            InvalidTaskTransition: If the transition is not permitted.
            ValueError: If new_status is not in ALLOWED_STATUSES.
        """
        if new_status not in ALLOWED_STATUSES:
            raise ValueError(
                f"Unknown status '{new_status}'. Allowed: {sorted(ALLOWED_STATUSES)}"
            )
        if new_status == self.status and allow_noop:
            return
        allowed = _VALID_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise InvalidTaskTransition(
                f"Cannot transition task '{self.task_id}' "
                f"from '{self.status}' → '{new_status}'"
            )
        self.status = new_status
        now = _now_iso()
        self.updated_at = now
        self.last_heartbeat_at = now
        if new_status in TERMINAL_STATUSES:
            self.terminal_at = now

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def heartbeat(self) -> None:
        """Update heartbeat timestamp without changing status."""
        now = _now_iso()
        self.updated_at = now
        self.last_heartbeat_at = now

    def add_step(self, step: StepRecord) -> None:
        """Append a step event and heartbeat."""
        self.steps.append(step)
        self.heartbeat()

    def add_provenance(self, *entry_ids: str) -> None:
        """Link evidence log entries to this task."""
        for eid in entry_ids:
            if eid and eid not in self.provenance_refs:
                self.provenance_refs.append(eid)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        d = asdict(self)
        # Flatten steps back to the legacy format expected by response_builder
        d["steps"] = [
            {
                "type": s.step_type,
                "iteration": s.iteration,
                "timestamp": s.timestamp,
                **s.details,
            }
            for s in self.steps
        ]
        return d

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Return a dict in the shape expected by existing TaskService/API clients.

        Preserves backward compatibility with code that reads
        ``conversation.active_tasks[i]`` directly.
        """
        legacy = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "action_name": self.action_name,
            "status": self.status,
            "next_trigger_at": self.next_trigger_at,
            "trigger_condition": self.trigger_condition,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "terminal_at": self.terminal_at,
            "_schema_version": self.schema_version,
        }
        # Inline structured fields into metadata for backward compat
        meta = legacy["metadata"]
        if self.steps:
            meta.setdefault(
                "steps",
                [
                    {
                        "type": s.step_type,
                        "iteration": s.iteration,
                        "timestamp": s.timestamp,
                        **s.details,
                    }
                    for s in self.steps
                ],
            )
        if self.failure_cause:
            meta.setdefault("failure_reason", self.failure_cause)
        if self.outputs:
            meta.update(self.outputs)
        if self.provenance_refs:
            meta.setdefault("provenance_refs", self.provenance_refs)
        return legacy

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRecord":
        """Reconstruct a TaskRecord from a persisted dict."""
        meta = dict(data.get("metadata") or {})
        raw_steps = meta.pop("steps", [])
        steps = [StepRecord.from_dict(s) for s in raw_steps if isinstance(s, dict)]

        return cls(
            task_id=str(data.get("task_id", uuid.uuid4().hex)),
            task_type=str(data.get("task_type", "")),
            description=str(data.get("description", "")),
            action_name=data.get("action_name"),
            status=str(data.get("status", "active")),
            schema_version=int(data.get("_schema_version", SCHEMA_VERSION)),
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            last_heartbeat_at=str(data.get("last_heartbeat_at", _now_iso())),
            terminal_at=data.get("terminal_at"),
            next_trigger_at=data.get("next_trigger_at"),
            trigger_condition=data.get("trigger_condition"),
            objective=meta.pop("objective", None),
            steps=steps,
            outputs={},
            failure_cause=meta.pop("failure_reason", None),
            retry_count=int(meta.pop("retry_count", 0)),
            provenance_refs=list(meta.pop("provenance_refs", [])),
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        description: str,
        task_type: str,
        action_name: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trigger_at: Optional[str] = None,
        trigger_condition: Optional[str] = None,
    ) -> "TaskRecord":
        """Convenience factory that auto-generates task_id if not supplied."""
        resolved_id = task_id
        if not resolved_id:
            short = uuid.uuid4().hex[:12]
            resolved_id = (
                f"{action_name}:{short}" if action_name else f"task_{uuid.uuid4().hex}"
            )
        return cls(
            task_id=resolved_id,
            task_type=task_type,
            description=description,
            action_name=action_name,
            status="active",
            next_trigger_at=trigger_at,
            trigger_condition=trigger_condition,
            metadata=dict(metadata or {}),
        )
