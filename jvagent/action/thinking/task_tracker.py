"""TaskTracker: structured step logging for agentic workflows.

Extends Conversation.active_tasks with detailed per-step tracking
for multi-step think-act-observe loops. Reuses Conversation.add_active_task()
and Conversation.update_task() directly.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskTracker:
    """Tracks multi-step agentic tasks on Conversation.active_tasks.

    TaskTracker is NOT an Action -- it is a runtime helper owned by
    ThinkingInteractAction during execute(). It creates one active_task
    per agentic loop invocation and records structured step data within
    the task's metadata field.

    Args:
        conversation: The Conversation node to track tasks on.
        action_name: The action class name for task attribution.
    """

    def __init__(
        self,
        conversation: Any,
        action_name: str = "ThinkingInteractAction",
    ) -> None:
        self._conversation = conversation
        self._action_name = action_name
        self._task_id: Optional[str] = None
        self._steps: List[Dict[str, Any]] = []
        self._created: bool = False
        self._start_time: Optional[float] = None
        self._tools_called: List[str] = []
        self._thinking_tokens_used: int = 0
        self._iteration_count: int = 0

    async def create_task(
        self,
        description: str,
        task_type: str = "AGENTIC_LOOP",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create an active_task on the Conversation.

        Args:
            description: Human-readable description of the agentic task.
            task_type: Type classification (default: AGENTIC_LOOP).
            metadata: Optional additional metadata.

        Returns:
            The task_id of the created task.
        """
        self._task_id = f"agentic:{uuid.uuid4().hex[:12]}"
        self._start_time = time.monotonic()
        now = datetime.now(timezone.utc).isoformat()

        task_metadata = {
            "task_type_detail": task_type,
            "skill": None,
            "iterations": 0,
            "tools_called": [],
            "thinking_tokens_used": 0,
            "steps": [],
            "started_at": now,
            "completed_at": None,
            "total_duration_seconds": None,
        }
        if metadata:
            task_metadata.update(metadata)

        await self._conversation.add_active_task(
            description=description,
            task_id=self._task_id,
            action_name=self._action_name,
            task_type=task_type,
            metadata=task_metadata,
        )
        self._created = True
        logger.debug("TaskTracker: created task %s", self._task_id)
        return self._task_id

    async def add_step(
        self,
        step_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a step within the agentic loop.

        Args:
            step_type: One of "thinking", "tool_call", "tool_result",
                "response", "error".
            iteration: Current loop iteration number.
            details: Optional dict with step-specific data.
        """
        if not self._created:
            return

        step = {
            "type": step_type,
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if details:
            step.update(details)

        self._steps.append(step)

        # Track aggregate data
        if step_type == "tool_call":
            tool_name = (details or {}).get("tool", "")
            if tool_name:
                self._tools_called.append(tool_name)
        if step_type == "thinking":
            tokens = (details or {}).get("tokens", 0)
            self._thinking_tokens_used += tokens
        self._iteration_count = max(self._iteration_count, iteration)

        # Persist to conversation metadata
        await self._sync_metadata()

    async def update_step(self, step_index: int, updates: Dict[str, Any]) -> None:
        """Update a specific step by index.

        Args:
            step_index: Index in the steps list.
            updates: Dict of fields to update on the step.
        """
        if not self._created or step_index >= len(self._steps):
            return

        self._steps[step_index].update(updates)
        await self._sync_metadata()

    async def complete_task(
        self,
        final_status: str = "completed",
        summary: Optional[str] = None,
    ) -> None:
        """Mark the task as completed/failed with final summary.

        Args:
            final_status: "completed", "failed", or "cancelled".
            summary: Optional final summary text.
        """
        if not self._created or not self._task_id:
            return

        now = datetime.now(timezone.utc).isoformat()
        duration = time.monotonic() - self._start_time if self._start_time else 0

        # Update the task metadata with final data
        task = self._conversation.get_active_task(task_id=self._task_id)
        if task:
            task_metadata = task.get("metadata", {})
            task_metadata.update(
                {
                    "iterations": self._iteration_count,
                    "tools_called": self._tools_called,
                    "thinking_tokens_used": self._thinking_tokens_used,
                    "steps": self._steps,
                    "completed_at": now,
                    "total_duration_seconds": round(duration, 2),
                    "final_summary": summary,
                }
            )
            # Upsert the task with updated metadata
            await self._conversation.add_active_task(
                description=task.get("description", ""),
                task_id=self._task_id,
                action_name=self._action_name,
                task_type=task.get("task_type"),
                metadata=task_metadata,
            )

        # Transition status
        await self._conversation.update_task(
            status=final_status,
            task_id=self._task_id,
        )
        logger.debug(
            "TaskTracker: completed task %s with status=%s (%.1fs, %d steps)",
            self._task_id,
            final_status,
            duration,
            len(self._steps),
        )

    async def fail_task(self, error: str) -> None:
        """Mark the task as failed with error description.

        Args:
            error: Description of the failure.
        """
        await self.add_step("error", details={"error": error})
        await self.complete_task(final_status="failed", summary=error)

    def get_progress_summary(self) -> Dict[str, Any]:
        """Return a summary dict suitable for adhoc streaming.

        Returns:
            Dict with iteration, steps_completed, tools_called, last_action.
        """
        last_step = self._steps[-1] if self._steps else {}
        return {
            "iteration": self._iteration_count,
            "steps_completed": len(self._steps),
            "tools_called": list(self._tools_called),
            "last_action": last_step.get("type", ""),
        }

    @property
    def task_id(self) -> Optional[str]:
        """Return the current task ID."""
        return self._task_id

    async def _sync_metadata(self) -> None:
        """Sync current step data to Conversation task metadata."""
        if not self._task_id:
            return

        task = self._conversation.get_active_task(task_id=self._task_id)
        if not task:
            return

        task_metadata = task.get("metadata", {})
        task_metadata.update(
            {
                "iterations": self._iteration_count,
                "tools_called": self._tools_called,
                "thinking_tokens_used": self._thinking_tokens_used,
                "steps": self._steps,
            }
        )

        await self._conversation.add_active_task(
            description=task.get("description", ""),
            task_id=self._task_id,
            action_name=self._action_name,
            task_type=task.get("task_type"),
            metadata=task_metadata,
        )
