"""Read completed native replies from existing graph state; never execute work."""

from typing import Any, AsyncIterator

REPLAY_INTERACTIONS = 20
REPLAY_TRACE_ENTRIES = 200


async def replay_completed_replies(
    conversation: Any, *, user_id: str
) -> AsyncIterator[dict[str, Any]]:
    """Project a bounded terminal history window into ordinary response frames.

    This is channel readback, not SDK execution replay or adapter redelivery.
    Uncertain/incomplete runs and historical rows without a delivery receipt
    are deliberately excluded. Each frame is reauthorized by the endpoint.
    """
    if conversation is None or conversation.user_id != user_id:
        return
    recent = await conversation.get_interactions(
        limit=REPLAY_INTERACTIONS, reverse=True
    )
    for interaction in reversed(recent):
        run = interaction.native_run
        delivery = run.get("delivery", {})
        run_id = run.get("run_id")
        if (
            interaction.conversation_id != conversation.id
            or interaction.session_id != conversation.session_id
            or interaction.user_id != user_id
            or run.get("status") != "delivered"
            or not interaction.closed
            or not interaction.has_emitted()
            or not interaction.response
            or not isinstance(run_id, str)
            or not isinstance(delivery, dict)
            or delivery.get("version") != 1
            or delivery.get("message_id") != f"o.ResponseMessage.{run_id}"
        ):
            continue
        from jvagent.harness.step_store import InteractionStepStore

        store = InteractionStepStore(interaction)
        record = await store.get_run(run_id=run_id)
        events = await store.list_events(run_id=run_id)
        if (
            record is None
            or record.parent_run_id
            or record.conversation_id != conversation.session_id
            or not events
            or events[-1].kind != "run_completed"
        ):
            continue
        # Do not return arbitrary persisted host/request metadata. This small
        # receipt was established by NativeHarnessAction before publication.
        metadata = delivery.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        common: dict[str, Any] = {
            "session_id": conversation.session_id,
            "user_id": user_id,
            "interaction_id": interaction.id,
            "message_type": "adhoc",
            "channel": delivery.get("channel", "default"),
        }
        for trace in interaction.agent_trace[-REPLAY_TRACE_ENTRIES:]:
            message_id = trace.get("message_id")
            phase = trace.get("thought_type")
            if (
                not isinstance(message_id, str)
                or not message_id.startswith("o.ResponseMessage.")
                or phase not in {"tool_call", "tool_result", "reasoning", "status"}
            ):
                continue
            yield {
                **common,
                "id": message_id,
                "content": trace.get("content", ""),
                "category": "thought",
                "thought_type": phase,
                "segment_id": trace.get("segment_id"),
                "timestamp": trace.get("started_at"),
                "metadata": {"native_replay": True},
            }
        yield {
            **common,
            "id": delivery["message_id"],
            "content": interaction.response,
            "category": "user",
            # Domain Interaction clocks may be app-local and naive. Use the
            # official terminal event's absolute instant for replay ordering.
            "timestamp": events[-1].timestamp.isoformat(),
            "metadata": {
                "native_replay": True,
                "is_proactive": metadata.get("is_proactive") is True,
                "observability": metadata.get("observability", {}),
                "replay_window": {
                    "interactions": REPLAY_INTERACTIONS,
                    "trace_entries": REPLAY_TRACE_ENTRIES,
                    "omitted_trace_entries": max(
                        0, len(interaction.agent_trace) - REPLAY_TRACE_ENTRIES
                    ),
                },
            },
        }
