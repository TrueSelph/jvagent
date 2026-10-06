"""Test-only approval and effect persistence adapter for PIL-08/PIL-09."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import Field

from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotModel,
    PilotSnapshot,
    validate_snapshot_for_run,
)
from jvagent.action.orchestrator.pilot.state import PilotStateError, PilotTaskStore
from jvagent.memory.task_store import TaskHandle


class EffectPilotInvocation(PilotModel):
    """Test-only invocation receipt omitted from production pilot tasks."""

    invocation_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    payload_digest: str = Field(min_length=1, max_length=128)
    status: Literal["prepared", "started", "settled"] = "prepared"
    result: Optional[str] = Field(default=None, max_length=16000)


class EffectPilotSnapshot(PilotSnapshot):
    """Test-only approval states excluded from the production snapshot API."""

    status: Literal[
        "running",
        "delivery_pending",
        "waiting_approval",
        "reconciliation_required",
        "complete",
        "failed",
        "cancelled",
        "parked",
    ] = "running"
    approval_id: Optional[str] = Field(default=None, max_length=256)
    approval_payload_digest: Optional[str] = Field(default=None, max_length=128)
    approval_expires_at: Optional[datetime] = None
    approval_invocation_id: Optional[str] = Field(default=None, max_length=256)
    requires_reconciliation: bool = False
    invocations: tuple[EffectPilotInvocation, ...] = Field(default=(), max_length=100)


class PilotEffectTestStore(PilotTaskStore):
    """Keep the excluded approval/effect state machine out of production code."""

    @staticmethod
    def _validate_snapshot(snapshot: PilotSnapshot) -> EffectPilotSnapshot:
        try:
            return EffectPilotSnapshot.model_validate(snapshot.model_dump(mode="json"))
        except Exception as exc:
            raise PilotStateError("pilot snapshot is invalid") from exc

    @staticmethod
    def _read_snapshot(handle: TaskHandle) -> EffectPilotSnapshot:
        raw_snapshot = handle.snapshot
        if not isinstance(raw_snapshot, dict):
            raise PilotStateError("pilot task snapshot is missing or invalid")
        try:
            snapshot = EffectPilotSnapshot.model_validate(raw_snapshot)
        except Exception as exc:
            raise PilotStateError("pilot task snapshot is invalid") from exc
        expected_task_status = {
            "running": "active",
            "delivery_pending": "active",
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

    async def record_invocation(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        invocation: EffectPilotInvocation,
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
