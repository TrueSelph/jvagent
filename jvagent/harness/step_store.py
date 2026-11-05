"""Interaction-scoped graph adapter for the official StepStore protocol.

Single active writer only. Distributed ownership/fencing must be established by
host admission before this adapter is used across workers. No replay engine lives
here: the official StepPersistence capability owns execution semantics.
"""

import asyncio
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)


class InteractionStepStore:
    """Persist public library records on a declared jvspatial Interaction field."""

    def __init__(self, interaction: Any) -> None:
        self.interaction = interaction
        self.lock = asyncio.Lock()

    def _records(self, key: str, record_type: Any) -> list[Any]:
        values = self.interaction.native_steps.get(key, [])
        return [TypeAdapter(record_type).validate_python(value) for value in values]

    async def _write(
        self, key: str, record: Any, *, identity: str | None = None
    ) -> None:
        async with self.lock:
            state = dict(self.interaction.native_steps)
            values = list(state.get(key, []))
            encoded = TypeAdapter(type(record)).dump_python(record, mode="json")
            if key == "runs":
                prior = next(
                    (v for v in values if v.get("run_id") == record.run_id), None
                )
                if prior is not None:
                    same_registration = encoded.get("registration_id") and all(
                        prior.get(field) == encoded.get(field)
                        for field in (
                            "registration_id",
                            "conversation_id",
                            "parent_run_id",
                            "agent_name",
                            "metadata",
                        )
                    )
                    if prior == encoded or same_registration:
                        return
                    raise ValueError("Native run identity is already registered")
            if identity is not None:
                values = [
                    value
                    for value in values
                    if not (
                        value.get("run_id") == record.run_id
                        and value.get(identity) == encoded.get(identity)
                    )
                ]
            elif encoded.get("idempotency_key") is not None:
                if any(
                    value.get("run_id") == record.run_id
                    and value.get("idempotency_key") == encoded["idempotency_key"]
                    for value in values
                ):
                    return
            values.append(encoded)
            state[key] = values
            self.interaction.native_steps = state
            await self.interaction.save()

    async def register_run(self, record: RunRecord) -> None:
        await self._write("runs", record, identity="run_id")

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        return next(
            (r for r in self._records("runs", RunRecord) if r.run_id == run_id), None
        )

    async def list_runs(
        self, *, parent_run_id: str | None = None, conversation_id: str | None = None
    ) -> list[RunRecord]:
        return sorted(
            (
                r
                for r in self._records("runs", RunRecord)
                if (parent_run_id is None or r.parent_run_id == parent_run_id)
                and (conversation_id is None or r.conversation_id == conversation_id)
            ),
            key=lambda r: r.started_at,
        )

    async def append_event(self, event: StepEvent) -> None:
        await self._write("events", event)

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        return [r for r in self._records("events", StepEvent) if r.run_id == run_id]

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        await self._write("snapshots", snapshot)

    async def first_snapshot(self, *, run_id: str) -> ContinuableSnapshot | None:
        """Read the earliest library checkpoint for host recovery admission.

        Storage extension only: public StepStore exposes the latest frontier,
        while a new user turn after failure needs the accepted-input frontier.
        No history is repaired, replayed or synthesized here.
        """
        return next(
            (
                r
                for r in self._records("snapshots", ContinuableSnapshot)
                if r.run_id == run_id
            ),
            None,
        )

    async def latest_snapshot(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> ContinuableSnapshot | None:
        return next(
            (
                r
                for r in reversed(self._records("snapshots", ContinuableSnapshot))
                if r.run_id == run_id and (include_interrupted or r.state == "complete")
            ),
            None,
        )

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        await self._write("effects", record, identity="tool_call_id")

    async def get_tool_effect(
        self, *, run_id: str, tool_call_id: str
    ) -> ToolEffectRecord | None:
        return next(
            (
                r
                for r in self._records("effects", ToolEffectRecord)
                if r.run_id == run_id and r.tool_call_id == tool_call_id
            ),
            None,
        )

    async def list_unresolved_tool_effects(
        self, *, run_id: str
    ) -> list[ToolEffectRecord]:
        return [
            r
            for r in self._records("effects", ToolEffectRecord)
            if r.run_id == run_id and r.status == "started"
        ]
