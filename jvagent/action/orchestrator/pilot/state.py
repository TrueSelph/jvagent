"""Strict adapter from typed pilot snapshots to the existing conversation TaskStore."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Optional

from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotInvocation,
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

    async def save(self, handle: TaskHandle, snapshot: PilotSnapshot) -> None:
        """Validate task ownership and persist the complete typed snapshot."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if handle.owner_action != snapshot.skill_id:
            raise PilotStateError("pilot snapshot skill does not match task owner")
        await handle.set_snapshot(snapshot.model_dump(mode="json"))

    async def record_invocation(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        invocation: PilotInvocation,
    ) -> PilotSnapshot:
        """Persist invocation intent before any effect may begin."""

        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if handle.status != "active" or snapshot.status != "running":
            raise PilotStateError("invocation intent requires an active pilot task")
        existing = next(
            (
                item
                for item in snapshot.invocations
                if item.invocation_id == invocation.invocation_id
            ),
            None,
        )
        if existing is not None:
            if existing != invocation:
                raise PilotStateError("invocation ID was reused with changed content")
            return snapshot
        if invocation.status != "prepared" or len(snapshot.invocations) >= 100:
            raise PilotStateError("invocation cannot be recorded in this state")
        updated = snapshot.model_copy(
            update={"invocations": (*snapshot.invocations, invocation)}
        )
        await self.save(handle, updated)
        return updated

    async def mark_invocation_started(
        self, handle: TaskHandle, snapshot: PilotSnapshot, invocation_id: str
    ) -> PilotSnapshot:
        """Durably mark the effect boundary before calling an external Action."""

        return await self._update_invocation(
            handle, snapshot, invocation_id, expected="prepared", status="started"
        )

    async def settle_invocation(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        invocation_id: str,
        result: str,
    ) -> PilotSnapshot:
        """Persist a bounded successful receipt before exposing it to the model."""

        if len(result) > 16000:
            raise PilotStateError("effect receipt exceeds the pilot size limit")
        return await self._update_invocation(
            handle,
            snapshot,
            invocation_id,
            expected="started",
            status="settled",
            result=result,
        )

    async def _update_invocation(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        invocation_id: str,
        *,
        expected: str,
        status: str,
        result: Optional[str] = None,
    ) -> PilotSnapshot:
        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if handle.status != "active" or snapshot.status != "running":
            raise PilotStateError("invocation receipt requires an active pilot task")
        matches = [
            item for item in snapshot.invocations if item.invocation_id == invocation_id
        ]
        if len(matches) != 1 or matches[0].status != expected:
            raise PilotStateError("invocation receipt transition is invalid")
        current = matches[0]
        changed = current.model_copy(update={"status": status, "result": result})
        updated = snapshot.model_copy(
            update={
                "invocations": tuple(
                    changed if item.invocation_id == invocation_id else item
                    for item in snapshot.invocations
                )
            }
        )
        await self.save(handle, updated)
        return updated

    async def park(
        self, handle: TaskHandle, snapshot: PilotSnapshot, reason: str
    ) -> None:
        """Park approval, reconciliation, or rollback work while retaining its state."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if snapshot.status not in {
            "waiting_approval",
            "reconciliation_required",
            "parked",
        }:
            raise PilotStateError("only waiting or rollback pilot work can be parked")
        parked = snapshot.model_copy(
            update={
                "status": "parked",
                "park_reason": reason[:512],
                "requires_reconciliation": snapshot.requires_reconciliation
                or snapshot.status == "reconciliation_required",
            }
        )
        parked = self._validate_snapshot(parked)
        await handle.park(snapshot=parked.model_dump(mode="json"), reason=reason[:512])

    async def wait_for_approval(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        *,
        invocation_id: str,
        approval_id: str,
        payload_digest: str,
        expires_at: datetime,
    ) -> PilotSnapshot:
        """Persist approval metadata and park before the interaction returns."""

        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        invocation = next(
            (
                item
                for item in snapshot.invocations
                if item.invocation_id == invocation_id
            ),
            None,
        )
        if (
            snapshot.status != "running"
            or invocation is None
            or invocation.status != "prepared"
            or invocation.payload_digest != payload_digest
            or not approval_id
            or expires_at.tzinfo is None
            or expires_at <= datetime.now(timezone.utc)
        ):
            raise PilotStateError("approval does not match a live prepared invocation")
        waiting = snapshot.model_copy(
            update={
                "status": "waiting_approval",
                "approval_id": approval_id,
                "approval_payload_digest": payload_digest,
                "approval_expires_at": expires_at,
                "approval_invocation_id": invocation_id,
            }
        )
        await self.park(handle, waiting, "waiting for explicit approval")
        return self._read_snapshot(handle)

    async def require_reconciliation(
        self, handle: TaskHandle, snapshot: PilotSnapshot, reason: str
    ) -> PilotSnapshot:
        """Park uncertain effect state so it cannot be resumed or replayed."""

        self._require_pilot_task(handle)
        snapshot = self._validate_snapshot(snapshot)
        if not any(item.status == "started" for item in snapshot.invocations):
            raise PilotStateError("reconciliation requires an unsettled invocation")
        uncertain = snapshot.model_copy(
            update={
                "status": "reconciliation_required",
                "requires_reconciliation": True,
            }
        )
        await self.park(handle, uncertain, reason)
        return self._read_snapshot(handle)

    async def resume(
        self,
        handle: TaskHandle,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
        approval_id: Optional[str] = None,
        approval_payload_digest: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> PilotSnapshot:
        """Resume an explicitly selected parked task after identity/digest checks."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
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
        if snapshot.status != "parked" or handle.status != "parked":
            raise PilotStateError("pilot task is not parked")
        if snapshot.requires_reconciliation:
            raise PilotStateError("effect reconciliation is required before resuming")
        if snapshot.approval_id:
            current_time = now or datetime.now(timezone.utc)
            expiry = snapshot.approval_expires_at
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if (
                approval_id != snapshot.approval_id
                or approval_payload_digest != snapshot.approval_payload_digest
                or expiry is None
                or current_time >= expiry
            ):
                raise PilotStateError("approval is missing, changed, or expired")
            approved_invocation = next(
                (
                    item
                    for item in snapshot.invocations
                    if item.invocation_id == snapshot.approval_invocation_id
                ),
                None,
            )
            if (
                approved_invocation is None
                or approved_invocation.status != "prepared"
                or approved_invocation.payload_digest != approval_payload_digest
            ):
                raise PilotStateError("approval does not match a prepared invocation")
        resumed = snapshot.model_copy(
            update={
                "status": "running",
                "park_reason": None,
                "approval_id": None,
                "approval_payload_digest": None,
                "approval_expires_at": None,
                "approval_invocation_id": None,
            }
        )
        resumed = self._validate_snapshot(resumed)
        await handle.resume_parked(snapshot=resumed.model_dump(mode="json"))
        return resumed

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
            or schema_version != 1
        ):
            if schema_version is None:
                version = "missing"
            else:
                version = repr(schema_version)[:32]
            raise PilotStateError(
                f"pilot task snapshot schema version {version} is unsupported; "
                "this pilot supports version 1 only. Preserve the task and "
                "start a new pilot run"
            )
        try:
            snapshot = PilotSnapshot.model_validate(raw_snapshot)
        except Exception as exc:
            raise PilotStateError(
                "pilot task snapshot is invalid for schema version 1; "
                "preserve the task and start a new pilot run"
            ) from exc
        expected_task_status = {
            "running": "active",
            "waiting_approval": "parked",
            "reconciliation_required": "parked",
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
