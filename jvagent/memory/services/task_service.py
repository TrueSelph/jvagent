"""Shared conversation task lifecycle service."""

import uuid
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from jvagent.memory.conversation import Conversation

ACTIVE_STATUSES = {"active", "pending", "triggered", "reserved"}
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "max_iterations",
    "superseded",
}
ALLOWED_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


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
    """Conversation-scoped task lifecycle service."""

    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation

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
        now = _now_iso()
        meta = dict(metadata or {})
        if trigger_at:
            meta["trigger_time"] = trigger_at
        if trigger_condition is not None:
            meta["trigger_condition"] = trigger_condition

        resolved_task_id = task_id
        if not resolved_task_id:
            short_uuid = uuid.uuid4().hex[:12]
            resolved_task_id = (
                f"{action_name}:{short_uuid}"
                if action_name
                else f"task_{uuid.uuid4().hex}"
            )

        entry: Dict[str, Any] = {
            "task_id": resolved_task_id,
            "task_type": task_type,
            "description": description,
            "action_name": action_name,
            "status": "active",
            "next_trigger_at": trigger_at or meta.get("trigger_time"),
            "trigger_condition": (
                trigger_condition
                if trigger_condition is not None
                else meta.get("trigger_condition")
            ),
            "metadata": meta,
            "created_at": now,
            "updated_at": now,
            "last_heartbeat_at": now,
            "terminal_at": None,
        }

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

        if existing_idx is not None:
            current = self.conversation.active_tasks[existing_idx]
            entry["task_id"] = current.get("task_id", resolved_task_id)
            entry["created_at"] = current.get("created_at", now)
            self.conversation.active_tasks[existing_idx] = entry
            await self.conversation.save()
            await self._emit_updated(entry)
            return TaskHandle(self, entry["task_id"])

        self.conversation.active_tasks.append(entry)
        await self.conversation.save()
        await self._emit_created(entry)
        return TaskHandle(self, entry["task_id"])

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
        steps = list(metadata.get("steps") or [])
        step = {
            "type": step_type,
            "iteration": iteration,
            "timestamp": _now_iso(),
        }
        if details:
            step.update(details)
        steps.append(step)
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
        if task.get("status") in TERMINAL_STATUSES:
            return True
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
