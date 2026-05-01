"""Shared conversation task lifecycle service.

Internally operates on typed ``TaskRecord`` objects with validated state
machine transitions.  External API surfaces (``list``, ``get``, ``start``,
etc.) still return plain dicts for backward compatibility with existing
callers; use ``get_record`` for typed access.

Tasks are dual-written: the authoritative ``TaskNode`` entity in the spatial
graph and the denormalized ``active_tasks`` list on Conversation for fast
context-window access.
"""

import logging
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from jvagent.memory.conversation import Conversation
from jvagent.memory.task_record import (
    ACTIVE_STATUSES,
    ALLOWED_STATUSES,
    TERMINAL_STATUSES,
    InvalidTaskTransition,
    StepRecord,
    TaskRecord,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class TaskHandle:
    """Handle bound to one task id for ergonomic lifecycle operations."""

    def __init__(self, service: "TaskService", task_id: str):
        self._service = service
        self.task_id = task_id

    async def record_step(
        self,
        step_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return await self._service.record_step(
            self.task_id, step_type=step_type, iteration=iteration, details=details
        )

    async def update_metadata(self, **patch: Any) -> bool:
        return await self._service.update_metadata(self.task_id, **patch)

    async def complete(
        self, status: str = "completed", summary: Optional[str] = None
    ) -> bool:
        return await self._service.complete(
            self.task_id, status=status, summary=summary
        )

    async def fail(self, error: str, status: str = "failed") -> bool:
        return await self._service.fail(self.task_id, error=error, status=status)

    async def cancel(self, reason: Optional[str] = None) -> bool:
        return await self._service.cancel(self.task_id, reason=reason)

    async def get(self) -> Optional[Dict[str, Any]]:
        return self._service.get(task_id=self.task_id)

    async def get_record(self) -> Optional[TaskRecord]:
        """Return the typed TaskRecord (preferred over the legacy dict)."""
        return self._service.get_record(task_id=self.task_id)


class _TaskTrackingContext(AbstractAsyncContextManager):
    """Context manager that guarantees terminal task status."""

    def __init__(
        self,
        service: "TaskService",
        *,
        description: str,
        task_type: str,
        action_name: Optional[str],
        task_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
        trigger_at: Optional[str],
        trigger_condition: Optional[str],
        singleton_action: bool,
        completion_status: str,
    ) -> None:
        self._service = service
        self._kwargs = {
            "description": description,
            "task_type": task_type,
            "action_name": action_name,
            "task_id": task_id,
            "metadata": metadata,
            "trigger_at": trigger_at,
            "trigger_condition": trigger_condition,
            "singleton_action": singleton_action,
        }
        self._completion_status = completion_status
        self._handle: Optional[TaskHandle] = None

    async def __aenter__(self) -> TaskHandle:
        self._handle = await self._service.start(**self._kwargs)
        return self._handle

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if not self._handle:
            return False
        task = self._service.get(task_id=self._handle.task_id)
        if task and task.get("status") in TERMINAL_STATUSES:
            return False
        if exc:
            await self._service.fail(self._handle.task_id, error=str(exc))
            return False
        await self._service.complete(
            self._handle.task_id, status=self._completion_status
        )
        return False


class TaskService:
    """Conversation-scoped task lifecycle service.

    Stores tasks as typed ``TaskRecord`` objects serialised to legacy dict
    format in ``conversation.active_tasks`` so existing consumers (API
    response builders, etc.) continue to work without changes.

    Use ``get_record`` / ``list_records`` for typed access; ``get`` / ``list``
    for backward-compatible plain-dict access.
    """

    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation
        self._agent_id: Optional[str] = None

    async def _get_agent_id(self) -> str:
        if self._agent_id is None:
            agent = await self.conversation.get_agent()
            self._agent_id = agent.id if agent else ""
        return self._agent_id

    async def _sync_task_node(
        self, task_dict: Dict[str, Any], *, node_id: Optional[str] = None
    ) -> Optional[str]:
        """Create or update a TaskNode from a legacy task dict.

        Stores the TaskNode's ID back into ``task_dict["metadata"]["_task_node_id"]``
        so subsequent updates can find the node without a separate query.

        Args:
            task_dict: The legacy dict from active_tasks (mutated in-place).
            node_id: Existing TaskNode ID to update, or None to create.

        Returns:
            The TaskNode ID if successful, None if sync failed (non-fatal).
        """
        try:
            from jvagent.memory.task_node import TaskNode

            agent_id = await self._get_agent_id()
            if not agent_id:
                return None

            if node_id:
                node = await TaskNode.get(node_id)
                if node:
                    node.task_id = task_dict.get("task_id", node.task_id)
                    node.task_type = task_dict.get("task_type", node.task_type)
                    node.description = task_dict.get("description", node.description)
                    node.action_name = task_dict.get("action_name", node.action_name)
                    node.status = task_dict.get("status", node.status)
                    node.next_trigger_at = task_dict.get(
                        "next_trigger_at", node.next_trigger_at
                    )
                    node.trigger_condition = task_dict.get(
                        "trigger_condition", node.trigger_condition
                    )
                    node.metadata = dict(task_dict.get("metadata") or {})
                    node.updated_at = task_dict.get("updated_at", node.updated_at)
                    node.last_heartbeat_at = task_dict.get(
                        "last_heartbeat_at", node.last_heartbeat_at
                    )
                    node.terminal_at = task_dict.get("terminal_at", node.terminal_at)
                    await node.save()
                    return node.id

            node = TaskNode.from_legacy_dict(
                task_dict,
                conversation_id=self.conversation.id,
                agent_id=agent_id,
            )
            await node.save()
            if not await self.conversation.is_connected_to(node):
                await self.conversation.connect(node, direction="out")
            # Store node id in the dict metadata for future sync calls
            meta = task_dict.setdefault("metadata", {})
            meta["_task_node_id"] = node.id
            return node.id
        except Exception:
            logger.debug(
                "Failed to sync TaskNode for task %s",
                task_dict.get("task_id"),
                exc_info=True,
            )
            return None

    async def _delete_task_node(self, node_id: str) -> None:
        """Cascade-delete a TaskNode."""
        try:
            from jvagent.memory.task_node import TaskNode

            node = await TaskNode.get(node_id)
            if node:
                await node.delete(cascade=True)
        except Exception:
            logger.debug("Failed to delete TaskNode %s", node_id, exc_info=True)

    @classmethod
    async def for_conversation(
        cls, conversation_or_id: Union[Conversation, str]
    ) -> "TaskService":
        if isinstance(conversation_or_id, Conversation):
            return cls(conversation_or_id)
        conversation = await Conversation.get(conversation_or_id)
        if not conversation:
            raise RuntimeError(f"Conversation not found: {conversation_or_id}")
        return cls(conversation)

    # ------------------------------------------------------------------
    # Typed access (preferred)
    # ------------------------------------------------------------------

    def get_record(
        self,
        *,
        task_id: Optional[str] = None,
        task_type: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Return a typed TaskRecord matching the filter criteria, or None."""
        raw = self.get(
            task_id=task_id,
            task_type=task_type,
            description=description,
            action_name=action_name,
            status=status,
        )
        if raw is None:
            return None
        try:
            return TaskRecord.from_dict(raw)
        except Exception:
            return None

    def list_records(
        self,
        status: Optional[Union[str, List[str]]] = None,
        action_name: Optional[str] = None,
    ) -> List[TaskRecord]:
        """Return typed TaskRecords, optionally filtered."""
        records: List[TaskRecord] = []
        for raw in self.list(status=status, action_name=action_name):
            try:
                records.append(TaskRecord.from_dict(raw))
            except Exception:
                pass
        return records

    # ------------------------------------------------------------------
    # Legacy dict access (backward compatible)
    # ------------------------------------------------------------------

    def list(
        self,
        status: Optional[Union[str, List[str]]] = None,
        action_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        tasks = list(getattr(self.conversation, "active_tasks", []))
        if status is not None:
            if isinstance(status, list):
                tasks = [t for t in tasks if t.get("status") in status]
            else:
                tasks = [t for t in tasks if t.get("status") == status]
        if action_name is not None:
            tasks = [t for t in tasks if t.get("action_name") == action_name]
        return tasks

    def get(
        self,
        *,
        task_id: Optional[str] = None,
        task_type: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        for task in getattr(self.conversation, "active_tasks", []):
            if task_id is not None and task.get("task_id") != task_id:
                continue
            if task_type is not None and task.get("task_type") != task_type:
                continue
            if description is not None and task.get("description") != description:
                continue
            if action_name is not None and task.get("action_name") != action_name:
                continue
            if status is not None and task.get("status") != status:
                continue
            return task
        return None

    async def start(
        self,
        *,
        description: str,
        task_type: str,
        action_name: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trigger_at: Optional[str] = None,
        trigger_condition: Optional[str] = None,
        singleton_action: bool = False,
    ) -> TaskHandle:
        meta = dict(metadata or {})
        if trigger_at:
            meta["trigger_time"] = trigger_at
        if trigger_condition is not None:
            meta["trigger_condition"] = trigger_condition

        record = TaskRecord.create(
            description=description,
            task_type=task_type,
            action_name=action_name,
            task_id=task_id,
            metadata=meta,
            trigger_at=trigger_at or meta.get("trigger_time"),
            trigger_condition=(
                trigger_condition
                if trigger_condition is not None
                else meta.get("trigger_condition")
            ),
        )
        resolved_task_id = record.task_id

        existing_idx = self._find_task_index(task_id=task_id) if task_id else None
        if existing_idx is None and singleton_action and action_name:
            active_existing_idx = self._find_task_index(
                action_name=action_name,
                status=list(ACTIVE_STATUSES),
            )
            if active_existing_idx is not None:
                old_task = self.conversation.active_tasks[active_existing_idx]
                if old_task.get("task_id") != resolved_task_id:
                    await self.complete(
                        old_task.get("task_id"),
                        status="superseded",
                        summary="Superseded by new singleton action task.",
                    )

        entry = record.to_legacy_dict()

        if existing_idx is not None:
            current = self.conversation.active_tasks[existing_idx]
            entry["task_id"] = current.get("task_id", resolved_task_id)
            entry["created_at"] = current.get("created_at", record.created_at)
            self.conversation.active_tasks[existing_idx] = entry
            await self.conversation.save()
            await self._sync_task_node(entry)
            await self._emit_updated(entry)
            return TaskHandle(self, entry["task_id"])

        self.conversation.active_tasks.append(entry)
        await self.conversation.save()
        # Dual-write: create TaskNode in the spatial graph
        await self._sync_task_node(entry)
        await self._emit_created(entry)
        return TaskHandle(self, record.task_id)

    def track(
        self,
        *,
        description: str,
        task_type: str,
        action_name: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trigger_at: Optional[str] = None,
        trigger_condition: Optional[str] = None,
        singleton_action: bool = False,
        completion_status: str = "completed",
    ) -> _TaskTrackingContext:
        return _TaskTrackingContext(
            self,
            description=description,
            task_type=task_type,
            action_name=action_name,
            task_id=task_id,
            metadata=metadata,
            trigger_at=trigger_at,
            trigger_condition=trigger_condition,
            singleton_action=singleton_action,
            completion_status=completion_status,
        )

    async def record_step(
        self,
        task_id: str,
        *,
        step_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        task = self.get(task_id=task_id)
        if not task:
            return False
        metadata = dict(task.get("metadata") or {})
        # Build typed StepRecord then serialise for metadata storage
        step_record = StepRecord(
            step_type=step_type,
            iteration=iteration,
            timestamp=_now_iso(),
            details=dict(details or {}),
        )
        step_dict = {
            "type": step_record.step_type,
            "iteration": step_record.iteration,
            "timestamp": step_record.timestamp,
            **step_record.details,
        }
        steps = list(metadata.get("steps") or [])
        steps.append(step_dict)
        metadata["steps"] = steps
        metadata["iterations"] = max(int(metadata.get("iterations", 0)), int(iteration))
        if step_type == "tool_call":
            tools_list = (details or {}).get("tools")
            if tools_list and isinstance(tools_list, list):
                tools_called = list(metadata.get("tools_called") or [])
                tools_called.extend(str(t) for t in tools_list)
                metadata["tools_called"] = tools_called
            else:
                tool_name = (details or {}).get("tool") or (details or {}).get(
                    "tool_name"
                )
                if tool_name:
                    tools_called = list(metadata.get("tools_called") or [])
                    tools_called.append(str(tool_name))
                    metadata["tools_called"] = tools_called
        if step_type == "thinking":
            tokens = int((details or {}).get("tokens", 0))
            metadata["thinking_tokens_used"] = (
                int(metadata.get("thinking_tokens_used", 0)) + tokens
            )
        return await self.update_metadata(task_id, **metadata)

    async def update_metadata(self, task_id: str, **patch: Any) -> bool:
        idx = self._find_task_index(task_id=task_id)
        if idx is None:
            return False
        now = _now_iso()
        task = dict(self.conversation.active_tasks[idx])
        metadata = dict(task.get("metadata") or {})
        metadata.update(patch)
        task["metadata"] = metadata
        task["updated_at"] = now
        task["last_heartbeat_at"] = now
        self.conversation.active_tasks[idx] = task
        await self.conversation.save()
        await self._emit_updated(task)
        await self._sync_task_node(
            task, node_id=task.get("metadata", {}).get("_task_node_id")
        )
        return True

    async def reserve(self, task_id: str) -> bool:
        idx = self._find_task_index(task_id=task_id, status=["active"])
        if idx is None:
            return False
        task = dict(self.conversation.active_tasks[idx])
        now = _now_iso()
        task["status"] = "reserved"
        task["updated_at"] = now
        task["last_heartbeat_at"] = now
        self.conversation.active_tasks[idx] = task
        await self.conversation.save()
        await self._emit_updated(task)
        await self._sync_task_node(
            task, node_id=task.get("metadata", {}).get("_task_node_id")
        )
        return True

    async def complete(
        self,
        task_id: str,
        *,
        status: str = "completed",
        summary: Optional[str] = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError(
                f"Invalid terminal status '{status}', expected one of {sorted(TERMINAL_STATUSES)}"
            )
        idx = self._find_task_index(task_id=task_id)
        if idx is None:
            return False
        task = dict(self.conversation.active_tasks[idx])
        # Idempotent: already terminal
        if task.get("status") in TERMINAL_STATUSES:
            return True
        # Validate transition via TaskRecord state machine
        try:
            record = TaskRecord.from_dict(task)
            record.transition(status)
        except InvalidTaskTransition as ite:
            logger.warning("TaskService.complete: %s", ite)
            return False
        now = _now_iso()
        metadata = dict(task.get("metadata") or {})
        if summary:
            metadata["final_summary"] = summary
        metadata.setdefault("completed_at", now)
        started_at = _parse_ts(metadata.get("started_at"))
        ended_at = _parse_ts(now)
        if started_at and ended_at:
            metadata["total_duration_seconds"] = round(
                (ended_at - started_at).total_seconds(), 2
            )
        task["metadata"] = metadata
        task["status"] = status
        task["updated_at"] = now
        task["last_heartbeat_at"] = now
        task["terminal_at"] = now
        self.conversation.active_tasks[idx] = task
        await self.conversation.save()
        await self._emit_terminal(task)
        await self._sync_task_node(task, node_id=metadata.get("_task_node_id"))
        return True

    async def fail(self, task_id: str, *, error: str, status: str = "failed") -> bool:
        if status not in TERMINAL_STATUSES:
            status = "failed"
        idx = self._find_task_index(task_id=task_id)
        if idx is None:
            return False
        task = self.conversation.active_tasks[idx]
        metadata = dict(task.get("metadata") or {})
        metadata["failure_reason"] = error
        await self.update_metadata(task_id, **metadata)
        return await self.complete(task_id, status=status, summary=error)

    async def cancel(self, task_id: str, *, reason: Optional[str] = None) -> bool:
        idx = self._find_task_index(task_id=task_id)
        if idx is None:
            return False
        if reason:
            task = self.conversation.active_tasks[idx]
            metadata = dict(task.get("metadata") or {})
            metadata["cancel_reason"] = reason
            await self.update_metadata(task_id, **metadata)
        return await self.complete(task_id, status="cancelled", summary=reason)

    async def update_status(
        self,
        *,
        status: str,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> bool:
        if status not in ALLOWED_STATUSES:
            status = "failed"
        idx = self._find_task_index(
            task_id=task_id,
            description=description,
            action_name=action_name,
        )
        if idx is None:
            return False
        task = dict(self.conversation.active_tasks[idx])
        now = _now_iso()
        task["status"] = status
        task["updated_at"] = now
        task["last_heartbeat_at"] = now
        if status in TERMINAL_STATUSES:
            task["terminal_at"] = now
        self.conversation.active_tasks[idx] = task
        await self.conversation.save()
        if status in TERMINAL_STATUSES:
            await self._emit_terminal(task)
        else:
            await self._emit_updated(task)
        await self._sync_task_node(
            task, node_id=task.get("metadata", {}).get("_task_node_id")
        )
        return True

    async def sweep_stale(self, ttl_seconds: int = 3600) -> int:
        now = datetime.now(timezone.utc)
        stale_indices: List[int] = []
        for idx, task in enumerate(getattr(self.conversation, "active_tasks", [])):
            if task.get("status") not in ACTIVE_STATUSES:
                continue
            heartbeat = (
                _parse_ts(task.get("last_heartbeat_at"))
                or _parse_ts(task.get("updated_at"))
                or _parse_ts(task.get("created_at"))
            )
            if not heartbeat:
                continue
            if (now - heartbeat).total_seconds() > ttl_seconds:
                stale_indices.append(idx)

        if not stale_indices:
            return 0

        completed_tasks: List[Dict[str, Any]] = []
        now_iso = _now_iso()
        for idx in stale_indices:
            task = dict(self.conversation.active_tasks[idx])
            metadata = dict(task.get("metadata") or {})
            metadata["failure_reason"] = "stale"
            task["metadata"] = metadata
            task["status"] = "failed"
            task["updated_at"] = now_iso
            task["terminal_at"] = now_iso
            task["last_heartbeat_at"] = now_iso
            self.conversation.active_tasks[idx] = task
            completed_tasks.append(task)
        await self.conversation.save()
        for task in completed_tasks:
            await self._emit_terminal(task)
            await self._sync_task_node(
                task, node_id=task.get("metadata", {}).get("_task_node_id")
            )
        return len(completed_tasks)

    def _find_task_index(
        self,
        *,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
        status: Optional[Union[str, List[str]]] = None,
    ) -> Optional[int]:
        statuses: Optional[List[str]]
        if isinstance(status, str):
            statuses = [status]
        else:
            statuses = status
        for idx, task in enumerate(getattr(self.conversation, "active_tasks", [])):
            if task_id and task.get("task_id") != task_id:
                continue
            if description and task.get("description") != description:
                continue
            if action_name and task.get("action_name") != action_name:
                continue
            if statuses is not None and task.get("status") not in statuses:
                continue
            return idx
        return None

    async def _emit_created(self, task_entry: Dict[str, Any]) -> None:
        try:
            from jvagent.core.callback import trigger_task_created_callback

            await trigger_task_created_callback(self.conversation, task_entry)
        except Exception:
            pass

    async def _emit_updated(self, task_entry: Dict[str, Any]) -> None:
        try:
            from jvagent.core.callback import trigger_task_updated_callback

            await trigger_task_updated_callback(self.conversation, task_entry)
        except Exception:
            pass

    async def _emit_terminal(self, task_entry: Dict[str, Any]) -> None:
        status = task_entry.get("status")
        try:
            if status == "completed":
                from jvagent.core.callback import trigger_task_completed_callback

                await trigger_task_completed_callback(self.conversation, task_entry)
            elif status == "failed":
                from jvagent.core.callback import trigger_task_failed_callback

                await trigger_task_failed_callback(self.conversation, task_entry)
            elif status == "cancelled":
                from jvagent.core.callback import trigger_task_cancelled_callback

                await trigger_task_cancelled_callback(self.conversation, task_entry)
            else:
                await self._emit_updated(task_entry)
        except Exception:
            pass
