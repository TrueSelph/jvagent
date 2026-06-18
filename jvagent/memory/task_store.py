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

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

logger = logging.getLogger(__name__)

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


# Map loose, model-supplied step statuses onto the canonical STEP_STATUSES.
_STEP_STATUS_ALIASES = {
    "todo": "pending",
    "pending": "pending",
    "not_started": "pending",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "active": "in_progress",
    "doing": "in_progress",
    "wip": "in_progress",
    "done": "done",
    "complete": "done",
    "completed": "done",
    "finished": "done",
    "skipped": "skipped",
    "skip": "skipped",
    "failed": "failed",
    "blocked": "failed",
    "error": "failed",
}


def normalize_step_status(raw: Any, default: str = "pending") -> str:
    """Coerce a loose status string to a canonical STEP_STATUSES value."""
    key = str(raw or "").strip().lower().replace(" ", "_")
    return _STEP_STATUS_ALIASES.get(
        key, default if default in STEP_STATUSES else "pending"
    )


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
        blocked_on: Task IDs that must be ``completed`` before this task is runnable
            (the work-stack/graph edge — ADR-0026). Empty ⇒ no prerequisites.
        resumes: The task ID that becomes runnable when THIS task completes (the
            back-link a prerequisite carries to its parent).
        order: FIFO tie-break among equally-eligible sibling tasks (lower first).
        seed: Opaque payload to (re)start the task — e.g. the originating utterance
            and captured inputs. The harness moves it; it never inspects it.
        snapshot: Durable runtime state for the task's owner (e.g. an interview's
            collected fields), so the live runtime can be torn down and rehydrated.
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
    blocked_on: List[str] = field(default_factory=list)
    resumes: Optional[str] = None
    order: int = 0
    seed: Dict[str, Any] = field(default_factory=dict)
    snapshot: Dict[str, Any] = field(default_factory=dict)

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
            {"completed", "failed", "cancelled", "pending"}
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
            blocked_on=[str(t) for t in (data.get("blocked_on") or []) if t],
            resumes=data.get("resumes"),
            order=int(data.get("order") or 0),
            seed=dict(data.get("seed") or {}),
            snapshot=dict(data.get("snapshot") or {}),
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

    @property
    def task_type(self) -> str:
        return self._task.task_type

    @property
    def owner_action(self) -> Optional[str]:
        return self._task.owner_action

    @property
    def updated_at(self) -> str:
        return self._task.updated_at

    # --- Work-graph (ADR-0026) ---

    @property
    def blocked_on(self) -> List[str]:
        return list(self._task.blocked_on)

    @property
    def resumes(self) -> Optional[str]:
        return self._task.resumes

    @property
    def order(self) -> int:
        return self._task.order

    @property
    def seed(self) -> Dict[str, Any]:
        return self._task.seed

    @property
    def snapshot(self) -> Dict[str, Any]:
        return self._task.snapshot

    async def set_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Persist durable runtime state for the task's owner (ADR-0026)."""
        self._task.snapshot = dict(snapshot or {})
        self._task._touch()
        await self._store._persist_task(self._task)

    async def set_seed(self, seed: Dict[str, Any]) -> None:
        """Persist the opaque restart payload (ADR-0026)."""
        self._task.seed = dict(seed or {})
        self._task._touch()
        await self._store._persist_task(self._task)

    async def add_blocker(self, task_id: str) -> None:
        """Add a prerequisite that must complete before this task is runnable."""
        if task_id and task_id not in self._task.blocked_on:
            self._task.blocked_on.append(str(task_id))
            self._task._touch()
            await self._store._persist_task(self._task)

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
        await self._store._emit_task_callback(self._task, "completed")

    async def fail(self, reason: Optional[str] = None) -> None:
        """Transition -> failed. Cascades: dependents blocked on this task are
        abandoned (they can never satisfy their prerequisite)."""
        self._task.transition("failed")
        if reason is not None:
            self._task.data["failure_reason"] = reason
        await self._store._persist_task(self._task)
        await self._store._emit_task_callback(self._task, "failed")
        await self._store._cascade_abandon_dependents(
            self._task.id, reason=f"prerequisite {self._task.id} failed"
        )

    async def cancel(self, reason: Optional[str] = None) -> None:
        """Transition -> cancelled. Cascades: dependents blocked on this task are
        abandoned (they can never satisfy their prerequisite)."""
        self._task.transition("cancelled")
        if reason is not None:
            self._task.data["cancel_reason"] = reason
        await self._store._persist_task(self._task)
        await self._store._emit_task_callback(self._task, "cancelled")
        await self._store._cascade_abandon_dependents(
            self._task.id, reason=f"prerequisite {self._task.id} cancelled"
        )

    async def update(self, **data: Any) -> None:
        """Merge key-value pairs into the task's data bag."""
        self._task.data.update(data)
        self._task._touch()
        await self._store._persist_task(self._task)
        await self._store._emit_task_callback(self._task, "updated")

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

    async def sync_plan(self, items: List[Dict[str, Any]]) -> List[StepHandle]:
        """Replace steps with an ordered plan carrying explicit statuses.

        Full-state overwrite in a **single persist** — the ergonomic shape for a
        model that re-sends its whole checklist each call (TodoWrite-style). Each
        item is a mapping with a ``description`` (or ``step``) and an optional
        ``status`` (loose values are normalized via ``normalize_step_status``);
        items without a description are skipped. Steps with a terminal status get
        a ``completed_at`` stamp. Empty/blank input clears the plan.

        An optional ``result`` (or ``note``/``outcome``) per item is carried onto
        the step so a later turn can RESUME from recorded work — e.g. an artifact
        path ("draft saved to report.md") — instead of redoing the step. It is
        bounded so the plan stays compact; large artifacts belong in a sandbox
        file referenced by the note, not inline here.
        """
        steps: List[Step] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            desc = str(it.get("description") or it.get("step") or it.get("title") or "")
            desc = desc.strip()
            if not desc:
                continue
            status = normalize_step_status(it.get("status"))
            step = Step(id=_new_id("step_"), description=desc, status=status)
            note = it.get("result") or it.get("note") or it.get("outcome")
            if note:
                step.result = str(note).strip()[:1000] or None
            if status in _STEP_TERMINAL:
                step.completed_at = step.updated_at
            steps.append(step)
        self._task.steps = steps
        self._task._touch()
        # Write the mutated task back into ``conversation.tasks`` by id (not a
        # bare save) so the new steps actually persist.
        await self._store._persist_task(self._task)
        return [StepHandle(self._store, self._task.id, s) for s in steps]

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

    def format_plan(self, with_results: bool = False) -> str:
        """Return a human-readable plan string.

        When ``with_results`` is set, each step's recorded ``result``/note is
        appended below it — used by the cross-turn resume note so a resumed turn
        sees what prior steps produced (e.g. artifact paths) instead of redoing
        them.
        """
        if not self._task.steps:
            return "(no steps)"
        lines = []
        for i, s in enumerate(self._task.steps, 1):
            entry = f"{i}. [{s.status}] {s.description}"
            if s.status == "skipped" and s.data.get("skip_reason"):
                entry += f" (skipped: {s.data['skip_reason']})"
            if with_results and s.result:
                entry += f"\n   ↳ {s.result}"
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
        dropped = 0
        for entry in raw:
            if not isinstance(entry, dict):
                dropped += 1
                continue
            try:
                tasks.append(Task.from_dict(entry))
            except Exception as exc:
                # A corrupt persisted task must not take down the whole turn,
                # but silently dropping it hides data loss — log for visibility.
                dropped += 1
                logger.warning(
                    "task_store: dropping unparseable task entry (id=%s): %s",
                    entry.get("id", "?"),
                    type(exc).__name__,
                )
        if dropped:
            logger.warning(
                "task_store: dropped %d corrupt task entr%s while loading",
                dropped,
                "y" if dropped == 1 else "ies",
            )
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
        blocked_on: Optional[List[str]] = None,
        resumes: Optional[str] = None,
        order: int = 0,
        seed: Optional[Dict[str, Any]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> TaskHandle:
        """Create a new pending task.

        ``blocked_on``/``resumes``/``seed``/``order``/``snapshot`` wire the task into
        the work graph (ADR-0026): a prerequisite is created with ``resumes`` pointing
        at its parent and the parent gains it as a blocker.
        """
        task = Task(
            id=task_id or _new_id("task_"),
            title=title,
            description=description,
            task_type=task_type or "",
            owner_action=owner_action,
            data=dict(data or {}),
            blocked_on=[str(t) for t in (blocked_on or []) if t],
            resumes=resumes,
            order=int(order or 0),
            seed=dict(seed or {}),
            snapshot=dict(snapshot or {}),
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

    async def _cascade_abandon_dependents(
        self, task_id: str, *, reason: str
    ) -> List[str]:
        """A task reached a non-completed terminal state (cancelled/failed); cancel
        every non-terminal task that is ``blocked_on`` it, transitively.

        ``prerequisites_met`` only treats a ``completed`` prerequisite as satisfied,
        so a dead (cancelled/failed) blocker would otherwise leave its dependents
        non-terminal yet permanently unrunnable — a zombie that keeps the engagement
        state True forever. Abandoning the chain is the correct gating semantics: if
        the prerequisite (e.g. a verify detour) is abandoned, the gated work it was
        a precondition for is abandoned too. Returns the ids cancelled.
        """
        abandoned: List[str] = []
        seen: set = set()
        frontier = [str(task_id)]
        while frontier:
            dead = frontier.pop()
            for task in self._load_tasks():
                if task.status in _TASK_TERMINAL or task.id in seen:
                    continue
                if dead in (task.blocked_on or []):
                    task.transition("cancelled")
                    task.data["cancel_reason"] = reason
                    await self._persist_task(task)
                    await self._emit_task_callback(task, "cancelled")
                    seen.add(task.id)
                    abandoned.append(task.id)
                    frontier.append(task.id)
        return abandoned

    # --- Utility ---

    def _task_entry_for_callback(self, task: Task) -> Dict[str, Any]:
        return {
            "task_id": task.id,
            "task_type": task.task_type,
            "description": task.description,
            "status": task.status,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "metadata": dict(task.data or {}),
            "next_trigger_at": (task.data or {}).get("not_before"),
        }

    async def _emit_task_callback(self, task: Task, event: str) -> None:
        try:
            from jvagent.core import callback as task_callbacks

            entry = self._task_entry_for_callback(task)
            conv = self._conversation
            if event == "created":
                await task_callbacks.trigger_task_created_callback(conv, entry)
            elif event == "updated":
                await task_callbacks.trigger_task_updated_callback(conv, entry)
            elif event == "completed":
                await task_callbacks.trigger_task_completed_callback(conv, entry)
            elif event == "failed":
                await task_callbacks.trigger_task_failed_callback(conv, entry)
            elif event == "cancelled":
                await task_callbacks.trigger_task_cancelled_callback(conv, entry)
        except Exception as exc:
            logger.debug("task_store: callback %s failed: %s", event, exc)

    async def enqueue_proactive(
        self,
        spec: Any,
        *,
        owner_action: Optional[str] = None,
        title: str = "",
    ) -> TaskHandle:
        """Create a pending PROACTIVE queue entry from a :class:`ProactiveTaskSpec`."""
        from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE, ProactiveTaskSpec

        if not isinstance(spec, ProactiveTaskSpec):
            spec = ProactiveTaskSpec.from_data(dict(spec or {}))
        spec.validate()
        label = (title or spec.directive or "").strip() or "Proactive task"
        handle = await self.create(
            title=label,
            description=label,
            owner_action=owner_action,
            task_type=PROACTIVE_TASK_TYPE,
            data=spec.to_data(),
        )
        await self._emit_task_callback(handle._task, "created")
        return handle

    async def claim_proactive(self, task_id: str, lease_id: str) -> bool:
        """Transition pending → active with a dispatch lease."""
        from jvagent.memory.task_eligibility import conversation_has_blockers
        from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE, ProactiveTaskSpec

        if conversation_has_blockers(self):
            return False

        handle = self.get(task_id)
        if handle is None:
            return False
        if handle.task_type != PROACTIVE_TASK_TYPE or handle.status != "pending":
            return False
        try:
            spec = ProactiveTaskSpec.from_task_handle(handle)
        except ValueError:
            return False
        spec.dispatch_lease_id = lease_id
        spec.dispatch_claimed_at = _now_iso()
        handle._task.data = spec.to_data()
        handle._task.transition("active")
        await self._persist_task(handle._task)
        await self._emit_task_callback(handle._task, "updated")
        return True

    async def requeue_proactive(self, task_id: str, reason: str) -> bool:
        """Transition active → pending and increment attempt_count."""
        from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE, ProactiveTaskSpec

        handle = self.get(task_id)
        if handle is None:
            return False
        if handle.task_type != PROACTIVE_TASK_TYPE or handle.status != "active":
            return False
        try:
            spec = ProactiveTaskSpec.from_task_handle(handle)
        except ValueError:
            return False
        spec.attempt_count = int(spec.attempt_count or 0) + 1
        spec.dispatch_lease_id = None
        spec.dispatch_claimed_at = None
        if reason:
            handle._task.data["last_requeue_reason"] = reason
        handle._task.data = spec.to_data()
        handle._task.transition("pending")
        await self._persist_task(handle._task)
        await self._emit_task_callback(handle._task, "updated")
        return True

    def list_queue(
        self,
        *,
        statuses: tuple = ("pending",),
    ) -> List[TaskHandle]:
        """List PROACTIVE queue entries sorted by priority then FIFO."""
        from jvagent.memory.task_eligibility import (
            _queue_sort_key,
            is_proactive_spec_task,
        )
        from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE

        handles = [
            h
            for h in self.list(status=list(statuses))
            if h.task_type == PROACTIVE_TASK_TYPE and is_proactive_spec_task(h)
        ]
        handles.sort(key=_queue_sort_key)
        return handles

    async def sweep_terminal(self, *, older_than_seconds: Optional[int] = None) -> int:
        """Remove terminal tasks, optionally older than a TTL.

        AUDIT-memory MED-10: when ``older_than_seconds`` is provided and
        a task's ``completed_at`` is unparseable, the previous logic
        kept the task silently. Behaviour now: log a warning AND keep
        the task (cannot prove it's old enough to evict). When
        ``older_than_seconds`` is None, ALL terminal tasks are evicted
        as the docstring implies.
        """
        tasks = self._load_tasks()
        now = datetime.now(timezone.utc)
        kept: List[Task] = []
        removed = 0
        for t in tasks:
            if t.status not in _TASK_TERMINAL:
                kept.append(t)
                continue
            if older_than_seconds is None:
                # Unconditional terminal sweep.
                removed += 1
                continue
            if not t.completed_at:
                # Conservative: keep tasks missing completed_at when a TTL is
                # set — we can't prove they're old enough to evict.
                kept.append(t)
                continue
            try:
                completed = datetime.fromisoformat(t.completed_at)
            except Exception:
                logger.warning(
                    "sweep_terminal: completed_at %r on task %s is not ISO; "
                    "skipping for safety",
                    t.completed_at,
                    getattr(t, "id", "?"),
                )
                kept.append(t)
                continue
            if (now - completed).total_seconds() > older_than_seconds:
                removed += 1
                continue
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
