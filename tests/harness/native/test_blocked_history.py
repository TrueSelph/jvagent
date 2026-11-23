"""Host recovery notices cannot replace or bypass the original SDK execution."""

from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai_harness.step_persistence import is_provider_valid

from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.memory.interaction import Interaction
from tests.harness.native.test_reconciliation import pending, reserve

NOTICE = "This conversation needs recovery before another model request can run. Start a new conversation or ask an administrator to reconcile the previous turn."


async def notice(previous: Interaction, text: str = "Can we continue?") -> Interaction:
    timestamp = previous.started_at + timedelta(seconds=1)
    item = await Interaction.create(
        conversation_id=previous.conversation_id,
        user_id=previous.user_id,
        session_id=previous.session_id,
        utterance=text,
        response=NOTICE,
        actions=["NativeHarnessAction"],
        closed=True,
        started_at=timestamp,
        completed_at=timestamp + timedelta(milliseconds=50),
        native_run={
            "status": "blocked",
            "reason": "predecessor_requires_reconciliation",
            "predecessor_interaction_id": previous.id,
        },
    )
    item.mark_emitted()
    await item.save()
    await previous.connect(item, direction="both")
    return item


@pytest.mark.asyncio
async def test_reconciled_root_retains_ordered_blocked_inputs(tmp_path: Path) -> None:
    root, conversation = await pending(tmp_path)
    first = await notice(root, "Please remember JUNIPER-28.")
    last = await notice(first, "Please remember MARIGOLD-91 too.")
    arguments = dict(
        conversation_id=conversation.id, user_id="caller", session_id="session"
    )
    with pytest.raises(
        NativeHistoryError, match="provider usage requires reconciliation"
    ):
        await history_for_new_turn(last, **arguments)
    await reserve(root, conversation)
    events = list(root.native_steps["events"])
    messages, source = await history_for_new_turn(last, **arguments)
    assert is_provider_valid(messages)
    assert source["source_run_id"] == "failed"
    assert source["blocked_interaction_ids"] == [first.id, last.id]
    text = str(messages)
    assert text.index("JUNIPER-28") < text.index("MARIGOLD-91")
    assert text.count("JUNIPER-28") == text.count("MARIGOLD-91") == 1
    assert root.native_steps["events"] == events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "user",
        "session",
        "conversation",
        "link",
        "steps",
        "metrics",
        "action",
        "open",
        "response",
        "run",
        "unemitted",
    ],
)
async def test_unqualified_notice_cannot_bypass_root(
    tmp_path: Path, tamper: str
) -> None:
    root, conversation = await pending(tmp_path)
    item = await notice(root)
    await reserve(root, conversation)
    if tamper == "user":
        item.user_id = "other"
    if tamper == "session":
        item.session_id = "other"
    if tamper == "conversation":
        item.conversation_id = "other"
    if tamper == "link":
        item.native_run = {**item.native_run, "predecessor_interaction_id": "wrong"}
    if tamper == "steps":
        item.native_steps = {"events": []}
    if tamper == "metrics":
        item.observability_metrics = [{"event_type": "model_call"}]
    if tamper == "action":
        item.actions = ["OtherAction"]
    if tamper == "open":
        item.closed = False
    if tamper == "response":
        item.response = "Unqualified model output"
    if tamper == "run":
        item.native_run = {**item.native_run, "run_id": "hidden-execution"}
    if tamper == "unemitted":
        item.emitted = False
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            item,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )


@pytest.mark.asyncio
async def test_pruned_root_and_excessive_notice_chain_fail_closed(
    tmp_path: Path,
) -> None:
    root, conversation = await pending(tmp_path)
    last = root
    for _ in range(33):
        last = await notice(last)
    with pytest.raises(NativeHistoryError, match="notice chain"):
        await history_for_new_turn(
            last,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )
    orphan = await notice(root)
    await root.disconnect(orphan)
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            orphan,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )


@pytest.mark.asyncio
async def test_notice_cycle_does_not_start_a_new_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, conversation = await pending(tmp_path)
    first = await notice(root)
    last = await notice(first)
    first.started_at = last.started_at
    first.native_run = {**first.native_run, "predecessor_interaction_id": last.id}

    async def cyclic(self: Interaction) -> Interaction:
        return first if self.id == last.id else last

    monkeypatch.setattr(Interaction, "get_previous_interaction", cyclic)
    with pytest.raises(NativeHistoryError, match="notice chain"):
        await history_for_new_turn(
            last,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )


@pytest.mark.asyncio
async def test_operator_inspection_finds_original_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from jvagent.action.native_harness import endpoints

    root, conversation = await pending(tmp_path)
    last = await notice(root)
    monkeypatch.setattr(
        endpoints, "_scope", AsyncMock(return_value=(None, last, conversation))
    )
    result = await endpoints.inspect_native_recovery("action", last.id)
    assert result["recovery_interaction_id"] == root.id
    assert result["blocked_interaction_ids"] == [last.id]
    with pytest.raises(ValueError):
        await reserve(last, conversation)
