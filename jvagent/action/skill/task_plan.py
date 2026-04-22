"""In-loop task plan state for task-tracker-driven execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


@dataclass
class TaskStep:
    """One tracked step in the in-loop task plan.

    Status values: pending | in_progress | done | skipped
    """

    id: int
    description: str
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"
    skip_reason: Optional[str] = field(default=None)


@dataclass
class InLoopTaskPlan:
    """Mutable task plan used as the loop's source of truth for progress."""

    steps: List[TaskStep]
    created_at_iteration: int

    def __post_init__(self) -> None:
        if self.steps and not any(step.status == "in_progress" for step in self.steps):
            first_pending = self._first_pending_step()
            if first_pending is not None:
                first_pending.status = "in_progress"

    def has_pending_steps(self) -> bool:
        """Return True when any step is not yet done or skipped."""
        return any(step.status not in ("done", "skipped") for step in self.steps)

    def pending_steps(self) -> List[TaskStep]:
        """Return steps that are not yet done or skipped."""
        return [step for step in self.steps if step.status not in ("done", "skipped")]

    def skipped_steps(self) -> List[TaskStep]:
        """Return steps that were explicitly skipped."""
        return [step for step in self.steps if step.status == "skipped"]

    def current_step(self) -> Optional[TaskStep]:
        for step in self.steps:
            if step.status == "in_progress":
                return step
        return self._first_pending_step()

    def step_label(self, step: TaskStep) -> str:
        return f"step {step.id}/{len(self.steps)}: {step.description}"

    def format_for_model(self) -> str:
        if not self.steps:
            return "(no tracked steps)"
        lines = []
        for step in self.steps:
            entry = f"{step.id}. [{step.status}] {step.description}"
            if step.status == "skipped" and step.skip_reason:
                entry += f" (skipped: {step.skip_reason})"
            lines.append(entry)
        return "\n".join(lines)

    def complete_step(self, step_id: int) -> bool:
        current = self.current_step()
        if current is None or current.id != step_id:
            return False

        current.status = "done"
        next_step = self._first_pending_step()
        if next_step is not None:
            next_step.status = "in_progress"
        return True

    def skip_step(self, step_id: int, reason: str) -> bool:
        """Mark the current in-progress step as skipped and advance the plan.

        Returns True on success, False if step_id does not match the current step.
        """
        current = self.current_step()
        if current is None or current.id != step_id:
            return False

        current.status = "skipped"
        current.skip_reason = reason
        next_step = self._first_pending_step()
        if next_step is not None:
            next_step.status = "in_progress"
        return True

    def to_checklist(self, *, pending_only: bool = False) -> List[Dict]:
        steps = self.pending_steps() if pending_only else self.steps
        result = []
        for step in steps:
            entry: Dict = {"item": step.description, "status": step.status}
            if step.status == "skipped" and step.skip_reason:
                entry["skip_reason"] = step.skip_reason
            result.append(entry)
        return result

    def _first_pending_step(self) -> Optional[TaskStep]:
        for step in self.steps:
            if step.status == "pending":
                return step
        return None
