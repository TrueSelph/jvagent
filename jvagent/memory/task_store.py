"""Conversation-scoped task and step tracking.

TaskStore provides a minimal, ergonomic API for creating, reading, updating,
and deleting tasks organized by steps.  All state lives on the Conversation
node as a typed ``tasks`` attribute.

Typical usage inside an action::

    store = TaskStore(conversation)
    task = await store.create(
        title="Analyze Q3 report",
        description="Fetch sales data and generate summary",
        owner_action="SkillAction",
    )
    await task.start()

    step = await task.add_step("Fetch sales CSV")
    await step.start()
    ... do work ...
    await step.complete(result="Loaded 4,200 rows")

    await task.complete(result="Report generated.")
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

# ------------------------------------------------------------------
# Status constants
# ------------------------------------------------------------------

TASK_STATUSES = frozenset({"pending", "active", "completed", "failed", "cancelled"})
STEP_STATUSES = frozenset({"pending", "in_progress", "done", "failed", "skipped"})

_TASK_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_STEP_TERMINAL = frozenset({"done", "failed", "skipped"})

# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------


class TaskError(ValueError):
    """Raised on invalid task or step lifecycle operations."""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else f"id_{short}"


# ------------------------------------------------------------------
# Step
# ------------------------------------------------------------------


@dataclass
class Step:
    """One step within a task.

    Attributes:
        id: Unique identifier within the parent task.
        description: Human-readable step description.
        status: Current status (pending, in_progress, done, failed, skipped).
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-updated timestamp.
        completed_at: ISO-8601 terminal timestamp (None while active).
        result: Free-text or structured outcome.
        data: Flexible extension bag.
    """

    id: str
    description: str
    status: Literal["pending", "in_progress", "done", "failed", "skipped"] = "pending"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    completed_at: Optional[str] = None
    result: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def _touch(self) -> None:
        self.updated_at = _now_iso()

    def transition(
        self, new_status: Literal["pending", "in_progress", "done", "failed", "skipped"]
    ) -> None:
        if new_status not in STEP_STATUSES:
            raise TaskError(f"Invalid step status '{new_status}'")
        if self.status in _STEP_TERMINAL and new_status != self.status:
            raise TaskError(
                f"Cannot transition step from '{self.status}' -> '{new_status}' (terminal)"
            )
        self.status = new_status
        self._touch()
        if new_status in _STEP_TERMINAL:
            self.completed_at = self.updated_at

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Step":
        return cls(
            id=str(data.get("id", _new_id("step_"))),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            completed_at=data.get("completed_at"),
            result=data.get("result"),
            data=dict(data.get("data") or {}),
        )


# ------------------------------------------------------------------
# Task
# ------------------------------------------------------------------


@dataclass
class Task:
    """One tracked task scoped to a conversation.

    Attributes:
        id: Unique task identifier.
        title: Short title for the task.
        description: Human-readable task description.
        status: Current status (pending, active, completed, failed, cancelled).
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-updated timestamp.
        completed_at: ISO-8601 terminal timestamp (None while active).
        owner_action: Name of the Action that owns this task.
        data: Flexible extension bag.
        steps: Ordered list of Step objects.
    """

    id: str
    title: str
    description: str
    status: Literal["pending", "active", "completed", "failed", "cancelled"] = "pending"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    completed_at: Optional[str] = None
    task_type: str = ""
    owner_action: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    steps: List[Step] = field(default_factory=list)

    def _touch(self) -> None:
        self.updated_at = _now_iso()

    def transition(
        self,
        new_status: Literal["pending", "active", "completed", "failed", "cancelled"],
    ) -> None:
        if new_status not in TASK_STATUSES:
            raise TaskError(f"Invalid task status '{new_status}'")
        if self.status in _TASK_TERMINAL and new_status != self.status:
            raise TaskError(
                f"Cannot transition task from '{self.status}' -> '{new_status}' (terminal)"
            )
        # Simple state machine enforcement
        if self.status == "pending" and new_status not in frozenset(
            {"active", "cancelled"}
        ):
            raise TaskError(f"Cannot transition task from 'pending' -> '{new_status}'")
        if self.status == "active" and new_status not in frozenset(
            {"completed", "failed", "cancelled"}
        ):
            raise TaskError(f"Cannot transition task from 'active' -> '{new_status}'")
        self.status = new_status
        self._touch()
        if new_status in _TASK_TERMINAL:
            self.completed_at = self.updated_at

    def add_step(self, description: str, data: Optional[Dict[str, Any]] = None) -> Step:
        step = Step(
            id=_new_id("step_"),
            description=description,
            data=dict(data or {}),
        )
        self.steps.append(step)
        self._touch()
        return step

    def get_step(self, step_id: str) -> Optional[Step]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def list_steps(self, status: Optional[Union[str, List[str]]] = None) -> List[Step]:
        steps = list(self.steps)
        if status is not None:
            if isinstance(status, str):
                statuses = {status}
            else:
                statuses = set(status)
            steps = [s for s in steps if s.status in statuses]
        return steps

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        raw_steps = data.get("steps") or []
        return cls(
            id=str(data.get("id", _new_id("task_"))),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),
            task_type=str(data.get("task_type", "")),
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            completed_at=data.get("completed_at"),
            owner_action=data.get("owner_action"),
            data=dict(data.get("data") or {}),
            steps=[Step.from_dict(s) for s in raw_steps if isinstance(s, dict)],
        )


# ------------------------------------------------------------------
# StepHandle
# ------------------------------------------------------------------


class StepHandle:
    """Ergonomic handle for mutating a single step."""

    def __init__(self, store: "TaskStore", task_id: str, step: Step):
        self._store = store
        self._task_id = task_id
        self._step = step

    @property
    def id(self) -> str:
        return self._step.id

    @property
    def description(self) -> str:
        return self._step.description

    @property
    def status(self) -> str:
        return self._step.status

    @property
    def result(self) -> Optional[str]:
        return self._step.result

    @property
    def data(self) -> Dict[str, Any]:
        return self._step.data

    async def start(self) -> None:
        """Transition pending -> in_progress."""
        self._step.transition("in_progress")
        await self._store._persist_step(self._step, self._task_id)

    async def complete(self, result: Optional[str] = None) -> None:
        """Transition -> done."""
        self._step.transition("done")
        if result is not None:
            self._step.result = result
        await self._store._persist_step(self._step, self._task_id)

    async def skip(self, reason: Optional[str] = None) -> None:
        """Transition -> skipped."""
        self._step.transition("skipped")
        if reason is not None:
            self._step.data["skip_reason"] = reason
        await self._store._persist_step(self._step, self._task_id)

    async def fail(self, reason: Optional[str] = None) -> None:
        """Transition -> failed."""
        self._step.transition("failed")
        if reason is not None:
            self._step.data["failure_reason"] = reason
        await self._store._persist_step(self._step, self._task_id)

    async def update(self, **data: Any) -> None:
        """Merge key-value pairs into the step's data bag."""
        self._step.data.update(data)
        self._step._touch()
        await self._store._persist_step(self._step, self._task_id)

    async def add_event(
        self,
        event_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an observability log entry to the step's data bag."""
        events = list(self._step.data.get("_events") or [])
        events.append(
            {
                "type": event_type,
                "iteration": iteration,
                "timestamp": _now_iso(),
                "details": dict(details or {}),
            }
        )
        self._step.data["_events"] = events
        self._step._touch()
        await self._store._persist_step(self._step, self._task_id)

    async def add_event(
        self,
        event_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an observability log entry to the step's data bag."""
        events = list(self._step.data.get("_events") or [])
        events.append(
            {
                "type": event_type,
                "iteration": iteration,
                "timestamp": _now_iso(),
                "details": dict(details or {}),
            }
        )
        self._step.data["_events"] = events
        self._step._touch()
        await self._store._persist_step(self._step, self._task_id)

    def to_dict(self) -> Dict[str, Any]:
        return self._step.to_dict()


# ------------------------------------------------------------------
# TaskHandle
# ------------------------------------------------------------------


class TaskHandle:
    """Ergonomic handle for mutating a single task and its steps."""

    def __init__(self, store: "TaskStore", task: Task):
        self._store = store
        self._task = task

    @property
    def id(self) -> str:
        return self._task.id

    @property
    def title(self) -> str:
        return self._task.title

    @property
    def description(self) -> str:
        return self._task.description

    @property
    def status(self) -> str:
        return self._task.status

    @property
    def data(self) -> Dict[str, Any]:
        return self._task.data

    # --- Task lifecycle ---

    async def start(self) -> None:
        """Transition pending -> active."""
        self._task.transition("active")
        await self._store._persist_task(self._task)

    async def complete(self, result: Optional[str] = None) -> None:
        """Transition -> completed."""
        self._task.transition("completed")
        if result is not None:
            self._task.data["result"] = result
        await self._store._persist_task(self._task)

    async def fail(self, reason: Optional[str] = None) -> None:
        """Transition -> failed."""
        self._task.transition("failed")
        if reason is not None:
            self._task.data["failure_reason"] = reason
        await self._store._persist_task(self._task)

    async def cancel(self, reason: Optional[str] = None) -> None:
        """Transition -> cancelled."""
        self._task.transition("cancelled")
        if reason is not None:
            self._task.data["cancel_reason"] = reason
        await self._store._persist_task(self._task)

    async def update(self, **data: Any) -> None:
        """Merge key-value pairs into the task's data bag."""
        self._task.data.update(data)
        self._task._touch()
        await self._store._persist_task(self._task)

    # --- Steps ---

    async def add_step(
        self, description: str, data: Optional[Dict[str, Any]] = None
    ) -> StepHandle:
        """Append a new step to this task."""
        step = self._task.add_step(description, data=data)
        await self._store._persist_task(self._task)
        return StepHandle(self._store, self._task.id, step)

    async def set_plan(
        self, descriptions: List[str], data: Optional[Dict[str, Any]] = None
    ) -> List[StepHandle]:
        """Replace steps with a new ordered plan.

        Existing steps are discarded; use ``add_step`` to append instead.
        The first step is automatically transitioned to ``in_progress``.
        """
        self._task.steps = [
            Step(id=_new_id("step_"), description=d, data=dict(data or {}))
            for d in descriptions
        ]
        if self._task.steps:
            self._task.steps[0].status = "in_progress"
        self._task._touch()
        await self._store._persist()
        return [StepHandle(self._store, self._task.id, s) for s in self._task.steps]

    def get_step(self, step_id: str) -> Optional[StepHandle]:
        step = self._task.get_step(step_id)
        if step is None:
            return None
        return StepHandle(self._store, self._task.id, step)

    def list_steps(
        self, status: Optional[Union[str, List[str]]] = None
    ) -> List[StepHandle]:
        return [
            StepHandle(self._store, self._task.id, s)
            for s in self._task.list_steps(status=status)
        ]

    def pending_steps(self) -> List[StepHandle]:
        return self.list_steps(status=["pending", "in_progress"])

    def current_step(self) -> Optional[StepHandle]:
        """Return the first in-progress or pending step."""
        for s in self._task.steps:
            if s.status == "in_progress":
                return StepHandle(self._store, self._task.id, s)
        for s in self._task.steps:
            if s.status == "pending":
                return StepHandle(self._store, self._task.id, s)
        return None

    def has_pending_steps(self) -> bool:
        return any(s.status not in _STEP_TERMINAL for s in self._task.steps)

    def get_step_by_index(self, idx: int) -> Optional[StepHandle]:
        """Return step by 1-based index."""
        if idx < 1 or idx > len(self._task.steps):
            return None
        return StepHandle(self._store, self._task.id, self._task.steps[idx - 1])

    def format_plan(self) -> str:
        """Return a human-readable plan string."""
        if not self._task.steps:
            return "(no steps)"
        lines = []
        for i, s in enumerate(self._task.steps, 1):
            entry = f"{i}. [{s.status}] {s.description}"
            if s.status == "skipped" and s.data.get("skip_reason"):
                entry += f" (skipped: {s.data['skip_reason']})"
            lines.append(entry)
        return "\n".join(lines)

    @property
    def steps(self) -> List[Step]:
        """Return raw step objects (internal; use list_steps for handles)."""
        return list(self._task.steps)

    def step_label(self, step: Step) -> str:
        idx = self._task.steps.index(step) + 1 if step in self._task.steps else 0
        return f"step {idx}/{len(self._task.steps)}: {step.description}"

    @property
    def task_plan(self) -> List[Dict[str, Any]]:
        """Return checklist representation of current steps (computed, not stored)."""
        return self.to_checklist()

    @property
    def task_plan_active(self) -> bool:
        """Whether any steps are still pending (computed, not stored)."""
        return self.has_pending_steps()

    @property
    def task_plan_pending_count(self) -> int:
        """Count of non-terminal steps (computed, not stored)."""
        return len(self.pending_steps())

    def to_checklist(self, *, pending_only: bool = False) -> List[Dict[str, Any]]:
        """Return checklist dicts for steps."""
        steps = self.pending_steps() if pending_only else self.list_steps()
        result: List[Dict[str, Any]] = []
        for sh in steps:
            entry: Dict[str, Any] = {"item": sh.description, "status": sh.status}
            if sh.status == "skipped" and sh.data.get("skip_reason"):
                entry["skip_reason"] = sh.data["skip_reason"]
            result.append(entry)
        return result

    # --- Observability events ---

    async def add_event(
        self,
        event_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an observability log entry to the task's data bag.

        Structured events (thinking, tool_call, etc.) are appended to a
        ``_events`` list inside ``task.data``.
        """
        events = list(self._task.data.get("_events") or [])
        events.append(
            {
                "type": event_type,
                "iteration": iteration,
                "timestamp": _now_iso(),
                "details": dict(details or {}),
            }
        )
        self._task.data["_events"] = events
        self._task._touch()
        await self._store._persist_task(self._task)

    def to_dict(self) -> Dict[str, Any]:
        return self._task.to_dict()


# ------------------------------------------------------------------
# TaskStore
# ------------------------------------------------------------------


class TaskStore:
    """Conversation-scoped task lifecycle store.

    All tasks are stored as typed ``Task`` objects serialised to dicts in
    ``conversation.tasks``.
    """

    def __init__(self, conversation: Any) -> None:
        self._conversation = conversation

    # --- Internal persistence ---

    async def _persist(self) -> None:
        await self._conversation.save()

    async def _persist_task(self, task: Task) -> None:
        """Persist a mutated task back into conversation.tasks by ID."""
        idx = self._find_task_index(task.id)
        if idx is not None:
            raw = list(getattr(self._conversation, "tasks", []) or [])
            raw[idx] = task.to_dict()
            self._conversation.tasks = raw
        await self._persist()

    async def _persist_step(self, step: Step, task_id: str) -> None:
        """Persist a mutated step by updating its parent task in the list."""
        idx = self._find_task_index(task_id)
        if idx is not None:
            raw = list(getattr(self._conversation, "tasks", []) or [])
            raw[idx] = self._task_to_dict_with_steps(task_id)
            self._conversation.tasks = raw
        await self._persist()

    def _task_to_dict_with_steps(self, task_id: str) -> Dict[str, Any]:
        for t in self._load_tasks():
            if t.id == task_id:
                return t.to_dict()
        return {}

    def _load_tasks(self) -> List[Task]:
        raw = getattr(self._conversation, "tasks", None) or []
        if not raw:
            return []
        tasks: List[Task] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                tasks.append(Task.from_dict(entry))
            except Exception:
                pass
        return tasks

    def _save_tasks(self, tasks: List[Task]) -> None:
        self._conversation.tasks = [t.to_dict() for t in tasks]

    def _find_task_index(self, task_id: str) -> Optional[int]:
        raw = getattr(self._conversation, "tasks", None) or []
        for idx, entry in enumerate(raw):
            if isinstance(entry, dict) and entry.get("id") == task_id:
                return idx
        return None

    # --- CRUD ---

    async def create(
        self,
        *,
        title: str,
        description: str,
        owner_action: Optional[str] = None,
        task_type: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> TaskHandle:
        """Create a new pending task."""
        task = Task(
            id=task_id or _new_id("task_"),
            title=title,
            description=description,
            task_type=task_type or "",
            owner_action=owner_action,
            data=dict(data or {}),
        )
        tasks = self._load_tasks()
        tasks.append(task)
        self._save_tasks(tasks)
        await self._persist()
        return TaskHandle(self, task)

    def get(self, task_id: str) -> Optional[TaskHandle]:
        """Get a task handle by id."""
        for task in self._load_tasks():
            if task.id == task_id:
                return TaskHandle(self, task)
        return None

    def list(
        self,
        status: Optional[Union[str, List[str]]] = None,
        owner_action: Optional[str] = None,
    ) -> List[TaskHandle]:
        """List tasks, optionally filtered."""
        tasks = self._load_tasks()
        if status is not None:
            if isinstance(status, str):
                statuses = {status}
            else:
                statuses = set(status)
            tasks = [t for t in tasks if t.status in statuses]
        if owner_action is not None:
            tasks = [t for t in tasks if t.owner_action == owner_action]
        return [TaskHandle(self, t) for t in tasks]

    async def delete(self, task_id: str) -> bool:
        """Remove a task permanently."""
        tasks = self._load_tasks()
        filtered = [t for t in tasks if t.id != task_id]
        if len(filtered) == len(tasks):
            return False
        self._save_tasks(filtered)
        await self._persist()
        return True

    # --- Utility ---

    async def sweep_terminal(self, *, older_than_seconds: Optional[int] = None) -> int:
        """Remove terminal tasks, optionally older than a TTL."""
        tasks = self._load_tasks()
        now = datetime.now(timezone.utc)
        kept: List[Task] = []
        removed = 0
        for t in tasks:
            if t.status not in _TASK_TERMINAL:
                kept.append(t)
                continue
            if older_than_seconds is not None and t.completed_at:
                try:
                    completed = datetime.fromisoformat(t.completed_at)
                    if (now - completed).total_seconds() > older_than_seconds:
                        removed += 1
                        continue
                except Exception:
                    pass
            kept.append(t)
        if removed:
            self._save_tasks(kept)
            await self._persist()
        return removed

    # --- Context manager helper ---

    def track(
        self,
        *,
        title: str,
        description: str,
        owner_action: Optional[str] = None,
        task_type: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        completion_result: Optional[str] = None,
    ) -> "_TaskTrackingContext":
        """Return an async context manager that guarantees terminal status."""
        return _TaskTrackingContext(
            self,
            title=title,
            description=description,
            owner_action=owner_action,
            task_type=task_type,
            data=data,
            task_id=task_id,
            completion_result=completion_result,
        )


# ------------------------------------------------------------------
# Tracking context
# ------------------------------------------------------------------


class _TaskTrackingContext:
    """Context manager that guarantees a terminal task status on exit."""

    def __init__(
        self,
        store: TaskStore,
        *,
        title: str,
        description: str,
        owner_action: Optional[str] = None,
        task_type: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        completion_result: Optional[str] = None,
    ):
        self._store = store
        self._kwargs = {
            "title": title,
            "description": description,
            "owner_action": owner_action,
            "task_type": task_type,
            "data": data,
            "task_id": task_id,
        }
        self._completion_result = completion_result
        self._handle: Optional[TaskHandle] = None

    async def __aenter__(self) -> TaskHandle:
        self._handle = await self._store.create(**self._kwargs)
        await self._handle.start()
        return self._handle

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if not self._handle:
            return False
        task = self._store.get(self._handle.id)
        if task is None:
            return False
        if task.status in _TASK_TERMINAL:
            return False
        if exc:
            await task.fail(reason=str(exc))
            return False
        await task.complete(result=self._completion_result)
        return False
