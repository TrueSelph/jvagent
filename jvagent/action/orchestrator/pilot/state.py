"""Strict adapter from typed pilot snapshots to the existing conversation TaskStore."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Optional

from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotSnapshot,
    ResearchBrief,
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

    def failed_retry_parent(
        self,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
        question: str,
    ) -> tuple[TaskHandle, PilotSnapshot] | None:
        """Find the latest exactly repeated failed objective with accounted use."""

        self._require_durable_conversation()
        for handle in reversed(
            self._store.list(status="failed", owner_action=skill_id)
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
            if snapshot.question == question and snapshot.proactive_task_id is None:
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

    def active_run(
        self,
        *,
        caller: PilotCaller,
        skill_id: str,
        skill_digest: str,
        config_digest: str,
    ) -> tuple[TaskHandle, PilotSnapshot] | None:
        """Find an unfinished run after the conversation lock admits a new turn.

        The caller owns the conversation mutation lock when using this method,
        so an active run for this conversation is an interrupted persisted run,
        not another live interaction.
        """

        self._require_durable_conversation()
        matches: list[tuple[TaskHandle, PilotSnapshot]] = []
        for handle in reversed(
            self._store.list(status="active", owner_action=skill_id)
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
            if snapshot.status in {"running", "delivery_pending"}:
                matches.append((handle, snapshot))
        if len(matches) > 1:
            raise PilotStateError(
                "multiple active pilot runs match this caller and configuration; "
                "preserve them and resolve the ambiguous recovery state before retry"
            )
        return matches[0] if matches else None

    async def fail_interrupted(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        *,
        reason: str = "interrupted run requires an explicit restart",
    ) -> PilotSnapshot:
        """Settle an orphaned active run while preserving its last checkpoint."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        current = self._read_snapshot(handle)
        if current != snapshot or handle.status != "active":
            raise PilotStateError("active pilot run changed during recovery")
        failed = current.model_copy(
            update={"status": "failed", "park_reason": reason[:512]}
        )
        await self.fail(handle, failed, reason)
        return failed

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
        if snapshot.status == "delivery_pending" and handle.status != "active":
            raise PilotStateError(
                "final delivery can only be prepared on an active task"
            )
        await handle.set_snapshot(snapshot.model_dump(mode="json"))

    async def prepare_delivery(
        self, handle: TaskHandle, snapshot: PilotSnapshot
    ) -> PilotSnapshot:
        """Persist validated final output before crossing the user egress boundary."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        if snapshot.status != "delivery_pending" or not isinstance(
            snapshot.output, ResearchBrief
        ):
            raise PilotStateError(
                "final delivery requires a pending evidence-backed ResearchBrief"
            )
        if handle.status != "active":
            raise PilotStateError("final delivery requires an active pilot task")
        current = self._read_snapshot(handle)
        if current.status != "running":
            raise PilotStateError(
                "pilot task changed before final delivery was prepared"
            )
        snapshot = self._validate_snapshot(
            snapshot.model_copy(
                update={
                    "delivery_attempt_count": 0,
                    "delivery_message_id": None,
                    "delivery_last_attempt_at": None,
                    "delivery_acknowledged": False,
                    "delivery_acknowledged_at": None,
                }
            )
        )
        try:
            output_user_text(snapshot.output, snapshot.evidence)
        except ValueError as exc:
            raise PilotStateError(
                "research pilot task output does not satisfy its evidence contract"
            ) from exc
        await handle.set_snapshot(snapshot.model_dump(mode="json"))
        return snapshot

    async def record_delivery_attempt(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        *,
        message_id: str,
    ) -> PilotSnapshot:
        """Persist the stable egress identity before an external send attempt."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        current = self._read_snapshot(handle)
        if (
            handle.status != "active"
            or current != snapshot
            or current.status != "delivery_pending"
            or current.delivery_acknowledged
        ):
            raise PilotStateError("pilot delivery changed before its send attempt")
        if (
            not isinstance(message_id, str)
            or not message_id.startswith("o.ResponseMessage.")
            or len(message_id) > 256
        ):
            raise PilotStateError("pilot delivery message identifier is invalid")
        if current.delivery_message_id not in (None, message_id):
            raise PilotStateError("pilot delivery identity changed during recovery")
        attempted = current.model_copy(
            update={
                "delivery_attempt_count": current.delivery_attempt_count + 1,
                "delivery_message_id": message_id,
                "delivery_last_attempt_at": datetime.now(timezone.utc),
            }
        )
        await handle.set_snapshot(attempted.model_dump(mode="json"))
        return attempted

    async def acknowledge_delivery(
        self,
        handle: TaskHandle,
        snapshot: PilotSnapshot,
        *,
        message_id: str,
    ) -> PilotSnapshot:
        """Persist an adapter/egress acknowledgment before terminal completion."""

        self._require_durable_conversation()
        self._require_pilot_task(handle)
        current = self._read_snapshot(handle)
        if (
            handle.status != "active"
            or current != snapshot
            or current.status != "delivery_pending"
            or current.delivery_attempt_count < 1
            or current.delivery_message_id != message_id
            or current.delivery_acknowledged
        ):
            raise PilotStateError("pilot delivery cannot be acknowledged in this state")
        acknowledged = current.model_copy(
            update={
                "delivery_acknowledged": True,
                "delivery_acknowledged_at": datetime.now(timezone.utc),
            }
        )
        await handle.set_snapshot(acknowledged.model_dump(mode="json"))
        return acknowledged

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
        if not isinstance(snapshot.output, ResearchBrief):
            raise PilotStateError(
                "research pilot task requires an evidence-backed ResearchBrief"
            )
        snapshot = self._validate_snapshot(snapshot)
        try:
            rendered_result = output_user_text(snapshot.output, snapshot.evidence)
        except ValueError as exc:
            raise PilotStateError(
                "research pilot task output does not satisfy its evidence contract"
            ) from exc
        current = self._read_snapshot(handle)
        if (
            handle.status != "active"
            or current.status != "delivery_pending"
            or not current.delivery_acknowledged
            or current.delivery_acknowledged_at is None
            or current.delivery_attempt_count < 1
            or current.delivery_message_id is None
            or snapshot.delivery_acknowledged is not True
            or snapshot.delivery_acknowledged_at != current.delivery_acknowledged_at
            or snapshot.delivery_message_id != current.delivery_message_id
            or snapshot.delivery_attempt_count != current.delivery_attempt_count
            or snapshot != current.model_copy(update={"status": "complete"})
        ):
            raise PilotStateError(
                "pilot task requires a persisted delivery acknowledgement"
            )
        await handle.complete(
            result=rendered_result,
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
            or schema_version not in (4, 5, 6, 7)
        ):
            if schema_version is None:
                version = "missing"
            else:
                version = repr(schema_version)[:32]
            raise PilotStateError(
                f"pilot task snapshot schema version {version} is unsupported; "
                "this pilot supports versions 4, 5, 6 and 7 only. Preserve the task and "
                "start a new pilot run"
            )
        if schema_version in (4, 5, 6):
            # Older snapshots lack the durable delivery receipt. Preserve that
            # uncertainty as unacknowledged; a pending output must be replayed
            # through the stable message ID before the task can complete.
            raw_snapshot = {
                **raw_snapshot,
                "schema_version": 7,
                "delivery_attempt_count": raw_snapshot.get("delivery_attempt_count", 0),
                "delivery_message_id": raw_snapshot.get("delivery_message_id"),
                "delivery_last_attempt_at": raw_snapshot.get(
                    "delivery_last_attempt_at"
                ),
                "delivery_acknowledged": False,
                "delivery_acknowledged_at": None,
            }
            if schema_version in (4, 5):
                # v4 lacked usage counters; v5 had counters but not an unsettled
                # request marker. Preserve known lower bounds and refuse to claim
                # unresolved provider usage was zero.
                raw_snapshot.update(
                    {
                        "usage_accounting_complete": False,
                        "unsettled_model_requests": 1,
                        "unreported_model_usage_responses": 0,
                    }
                )
            if schema_version == 4:
                raw_snapshot.update(
                    {
                        "model_requests_used": 0,
                        "tool_calls_used": 0,
                        "reported_input_tokens_used": 0,
                        "reported_output_tokens_used": 0,
                        "estimated_input_tokens_used": 0,
                        "estimated_output_tokens_used": 0,
                    }
                )
        try:
            snapshot = PilotSnapshot.model_validate(raw_snapshot)
        except Exception as exc:
            raise PilotStateError(
                "pilot task snapshot is invalid for its declared schema version; "
                "preserve the task and start a new pilot run"
            ) from exc
        expected_task_status = {
            "running": "active",
            "delivery_pending": "active",
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
