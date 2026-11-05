"""Host admission for a fresh user turn, never automatic execution replay."""

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart
from pydantic_ai_harness.step_persistence import continue_run, is_provider_valid
from pydantic_ai_harness.step_persistence.recovery import inspect_recovery

from jvagent.harness.step_store import InteractionStepStore

INPUT_CHECKPOINT_POLICY = "accepted-input-v1"


class NativeHistoryError(RuntimeError):
    """The predecessor cannot safely supply this conversation's context."""


async def history_for_new_turn(
    previous: Any, *, conversation_id: str, user_id: str, session_id: str
) -> tuple[list[ModelMessage], dict[str, str]]:
    """Use delivered history or a terminal failed run's accepted input.

    The latter discards all outputs of the failed run, including paid but
    undelivered answers. Existing receipts and effect records remain intact.
    Interrupted workers, unresolved effects and uncertain delivery require
    explicit reconciliation instead. Admission must hold the conversation lock.
    """
    if previous is None:
        return [], {}
    if not previous.native_run:
        raise NativeHistoryError("Previous interaction lacks qualified native history")
    if previous.conversation_id != conversation_id or previous.user_id != user_id:
        raise NativeHistoryError("Native history belongs to another caller")
    run = previous.native_run
    identity = run.get("run_id")
    store = InteractionStepStore(previous)
    record = await store.get_run(run_id=identity)
    if record is None or record.conversation_id != session_id or record.parent_run_id:
        raise NativeHistoryError("Native history has no matching root registration")
    if run.get("status") == "delivered":
        settled = await store.latest_snapshot(run_id=identity)
        if settled is None or settled.conversation_id != session_id:
            raise NativeHistoryError("Native history has no scoped delivery checkpoint")
        messages = await continue_run(store, run_id=identity)
        return messages, {"source_run_id": identity, "frontier": "delivered"}
    if run.get("status") not in {"failed", "cancelled"}:
        raise NativeHistoryError("Previous native run requires reconciliation")
    if record.metadata.get("jv_checkpoint_policy") != INPUT_CHECKPOINT_POLICY:
        raise NativeHistoryError(
            "Previous native run has no qualified input checkpoint"
        )

    records = await store.list_runs()
    owned = {identity}
    while True:
        expanded = owned | {r.run_id for r in records if r.parent_run_id in owned}
        if expanded == owned:
            break
        owned = expanded
    if owned != {r.run_id for r in records}:
        raise NativeHistoryError("Native history contains unowned execution records")
    for item in records:
        if item.conversation_id != session_id:
            raise NativeHistoryError("Native child belongs to another conversation")
        events = await store.list_events(run_id=item.run_id)
        if not events or events[-1].kind not in {"run_completed", "run_failed"}:
            raise NativeHistoryError("Previous native execution is not terminal")
        from jvagent.harness.conversation_tools import CONVERSATION_EFFECT

        for event in events:
            if event.kind != "tool_call_started" or not event.tool_call_id:
                continue
            effect = await store.get_tool_effect(
                run_id=item.run_id, tool_call_id=event.tool_call_id
            )
            if effect is not None and effect.effect_summary == CONVERSATION_EFFECT:
                raise NativeHistoryError(
                    "Previous conversation mutation requires reconciliation"
                )
        inspection = await inspect_recovery(store=store, run_id=item.run_id)
        if inspection.unresolved:
            raise NativeHistoryError(
                "Previous native tool effects require reconciliation"
            )

    snapshot = await store.first_snapshot(run_id=identity)
    if (
        snapshot is None
        or snapshot.state != "complete"
        or snapshot.conversation_id != session_id
        or not is_provider_valid(snapshot.messages)
        or any(
            isinstance(m, ModelResponse) and m.run_id == identity
            for m in snapshot.messages
        )
        or not any(
            isinstance(p, UserPromptPart) and p.content == previous.utterance
            for m in snapshot.messages
            for p in m.parts
        )
    ):
        raise NativeHistoryError(
            "Previous native run has no safe accepted-input frontier"
        )
    started = [
        e
        for e in await store.list_events(run_id=identity)
        if e.kind == "model_request_started"
    ]
    if started and snapshot.timestamp > started[0].timestamp:
        raise NativeHistoryError(
            "Native input checkpoint was captured after execution began"
        )

    from jvagent.harness.accounting import recover_native_usage

    await recover_native_usage(previous, run_id=identity, model="unknown")
    await previous.save()
    from jvspatial import flush_deferred_entities

    await flush_deferred_entities(previous, strict=True)
    from jvagent.harness.reconciliation import has_usage_reserve

    if previous.usage.get("unknown_cost_call_count", 0) and not has_usage_reserve(
        previous
    ):
        raise NativeHistoryError("Previous provider usage requires reconciliation")
    messages = list(snapshot.messages)
    if previous.has_emitted() and previous.response:
        # This is the observed JV response (usually a canned failure notice),
        # not an unacknowledged model response from the failed run's snapshots.
        messages.append(
            ModelResponse(parts=[TextPart(previous.response)], run_id=identity)
        )
    return messages, {"source_run_id": identity, "frontier": "accepted_input"}
