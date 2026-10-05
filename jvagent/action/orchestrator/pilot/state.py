"""Strict adapter from typed pilot snapshots to the existing conversation TaskStore."""

from __future__ import annotations

import inspect
from typing import Any, Optional

from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotSnapshot,
    output_user_text,
    validate_snapshot_for_run,
)
from jvagent.memory.task_store import TaskHandle, TaskStore

PILOT_TASK_TYPE = "CAPABILITY_PILOT"


class PilotStateError(RuntimeError):
    """Persisted pilot state is missing, stale, or cannot be safely saved."""


class PilotTaskStore:
    """Persist one pilot run per TaskStore task without a second state database."""

    def __init__(self, conversation: Any) -> None:
        self._conversation = conversation
        self._store = TaskStore(conversation)

    def _require_durable_conversation(self) -> None:
        """Reject test doubles or adapters where TaskStore persistence is a no-op."""

        durable_method = any(
            inspect.iscoroutinefunction(getattr(self._conversation, name, None))
            for name in ("flush", "save")
        )
        if not durable_method:
            raise PilotStateError(
                "conversation must provide asynchronous flush() or save()"
            )

    async def create(
        self,
        snapshot: PilotSnapshot,
        *,
        title: str,
        description: str,
        task_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        correlation_id: str = "",
    ) -> TaskHandle:
        """Create and start one isolated task, linked to prior evidence if supplied."""

        self._require_durable_conversation()
        snapshot = self._validate_snapshot(snapshot)
        data: dict[str, Any] = {}
        if correlation_id:
            data["pilot_correlation_id"] = correlation_id[:128]
        if parent_task_id:
            parent = self._store.get(parent_task_id)
            if parent is None or parent.task_type != PILOT_TASK_TYPE:
                raise PilotStateError("pilot follow-up parent task is unavailable")
            parent_snapshot = self._read_snapshot(parent)
            if parent_snapshot.caller != snapshot.caller:
                raise PilotStateError("pilot follow-up caller does not match parent")
            data["pilot_parent_task_id"] = parent_task_id
        handle = await self._store.create(
            title=title,
            description=description,
            owner_action=snapshot.skill_id,
            task_type=PILOT_TASK_TYPE,
            task_id=task_id,
            initial_status="active",
            data=data,
            snapshot=snapshot.model_dump(mode="json"),
        )
        return handle

    def load(
        self,
        task_id: str,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
    ) -> tuple[TaskHandle, PilotSnapshot]:
        """Load only a pilot-owned task matching caller and current compiled inputs."""

        self._require_durable_conversation()
        handle = self._store.get(task_id)
        if handle is None or handle.task_type != PILOT_TASK_TYPE:
            raise PilotStateError("pilot task is unavailable")
        snapshot = self._read_snapshot(handle)
        try:
            validate_snapshot_for_run(
                snapshot,
                caller=caller,
                skill_id=skill_id,
                skill_digest=skill_digest,
                config_digest=config_digest,
            )
        except ValueError as exc:
            raise PilotStateError(str(exc)) from exc
        return handle, snapshot

    def latest_completed(
        self,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
    ) -> tuple[TaskHandle, PilotSnapshot] | None:
        """Find the newest settled run with matching identity and inputs."""

        self._require_durable_conversation()
        for handle in reversed(self._store.list(owner_action=skill_id)):
            if handle.task_type != PILOT_TASK_TYPE or handle.status != "completed":
                continue
            try:
                snapshot = self._read_snapshot(handle)
                validate_snapshot_for_run(
                    snapshot,
                    caller=caller,
                    skill_id=skill_id,
                    skill_digest=skill_digest,
                    config_digest=config_digest,
                )
            except (PilotStateError, ValueError):
                continue
            return handle, snapshot
        return None

    def parked_retry(
        self,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
        question: str,
    ) -> tuple[TaskHandle, PilotSnapshot] | None:
        """Find the unique parked read-only run explicitly repeated by its caller."""

        self._require_durable_conversation()
        matches: list[tuple[TaskHandle, PilotSnapshot]] = []
        for handle in reversed(
            self._store.list(status="parked", owner_action=skill_id)
        ):
            if handle.task_type != PILOT_TASK_TYPE:
                continue
            try:
                snapshot = self._read_snapshot(handle)
                validate_snapshot_for_run(
                    snapshot,
                    caller=caller,
                    skill_id=skill_id,
                    skill_digest=skill_digest,
                    config_digest=config_digest,
                )
            except (PilotStateError, ValueError):
                continue
            if (
                snapshot.status == "parked"
                and snapshot.proactive_task_id is None
                and snapshot.question == question
            ):
                matches.append((handle, snapshot))
        return matches[0] if len(matches) == 1 else None

    async def resume_parked_retry(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
        question: str,
        correlation_id: str = "",
    ) -> PilotSnapshot:
        """Resume one exact read-only retry after fresh identity/input checks."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        current = self._read_snapshot(handle)
        if current != snapshot or handle.status != "parked":
            raise PilotStateError("parked pilot run changed before it could resume")
        try:
            validate_snapshot_for_run(
                current,
                caller=caller,
                skill_id=skill_id,
                skill_digest=skill_digest,
                config_digest=config_digest,
            )
        except ValueError as exc:
            raise PilotStateError(str(exc)) from exc
        if current.question != question or current.proactive_task_id is not None:
            raise PilotStateError("pilot retry does not match the parked request")
        resumed = current.model_copy(update={"status": "running", "park_reason": None})
        if correlation_id:
            await handle.update(pilot_correlation_id=correlation_id[:128])
        await handle.resume_parked(snapshot=resumed.model_dump(mode="json"))
        return resumed

    async def save(self, handle: TaskHandle, snapshot: PilotSnapshot) -> None:
        """Validate task ownership and persist the complete typed snapshot."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if handle.owner_action != snapshot.skill_id:
            raise PilotStateError("pilot snapshot skill does not match task owner")
        await handle.set_snapshot(snapshot.model_dump(mode="json"))

    async def complete(
        self, handle: TaskHandle, snapshot: PilotSnapshot, *, delivered: bool
    ) -> None:
        """Complete only after validated output has actually reached the egress path."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        if not delivered or snapshot.status != "complete" or snapshot.output is None:
            raise PilotStateError(
                "pilot task requires validated output and final delivery"
            )
        snapshot = self._validate_snapshot(snapshot)
        await handle.complete(
            result=output_user_text(snapshot.output, snapshot.evidence),
            snapshot=snapshot.model_dump(mode="json"),
        )

    async def fail(
        self, handle: TaskHandle, snapshot: PilotSnapshot, reason: str
    ) -> None:
        """Persist failure before marking the task terminal."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        if snapshot.status != "failed":
            raise PilotStateError(
                "failed TaskStore status requires failed pilot snapshot"
            )
        snapshot = self._validate_snapshot(snapshot)
        await handle.fail(
            reason=reason[:512], snapshot=snapshot.model_dump(mode="json")
        )

    async def cancel(
        self, handle: TaskHandle, snapshot: PilotSnapshot, reason: str
    ) -> None:
        """Persist a cancelled pilot snapshot before the TaskStore transition."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if snapshot.status != "cancelled":
            raise PilotStateError(
                "cancelled TaskStore status requires cancelled snapshot"
            )
        await handle.cancel(
            reason=reason[:512], snapshot=snapshot.model_dump(mode="json")
        )

    @staticmethod
    def _require_pilot_task(handle: TaskHandle) -> None:
        if handle.task_type != PILOT_TASK_TYPE:
            raise PilotStateError("TaskStore task is not owned by the capability pilot")

    @staticmethod
    def _read_snapshot(handle: TaskHandle) -> PilotSnapshot:
        raw_snapshot = handle.snapshot
        if not isinstance(raw_snapshot, dict):
            raise PilotStateError(
                "pilot task snapshot is missing or invalid; preserve the task "
                "and start a new pilot run"
            )
        schema_version = raw_snapshot.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 3
        ):
            if schema_version is None:
                version = "missing"
            else:
                version = repr(schema_version)[:32]
            raise PilotStateError(
                f"pilot task snapshot schema version {version} is unsupported; "
                "this pilot supports version 3 only. Preserve the task and "
                "start a new pilot run"
            )
        try:
            snapshot = PilotSnapshot.model_validate(raw_snapshot)
        except Exception as exc:
            raise PilotStateError(
                "pilot task snapshot is invalid for schema version 3; "
                "preserve the task and start a new pilot run"
            ) from exc
        expected_task_status = {
            "running": "active",
            "parked": "parked",
            "complete": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[snapshot.status]
        if handle.status != expected_task_status:
            raise PilotStateError(
                "pilot task and snapshot lifecycle statuses do not match"
            )
        return snapshot

    @staticmethod
    def _validate_snapshot(snapshot: PilotSnapshot) -> PilotSnapshot:
        try:
            return PilotSnapshot.model_validate(snapshot.model_dump(mode="json"))
        except Exception as exc:
            raise PilotStateError("pilot snapshot is invalid") from exc
