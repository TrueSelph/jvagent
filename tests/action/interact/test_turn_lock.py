"""Conversation turn lock: reentrancy and in-process serialization."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from jvagent.memory.distributed_conversation_lock import (
    conversation_mutation_lock,
    holds_conversation_mutation_lock,
)


async def test_holds_conversation_mutation_lock_reentrant():
    conv_id = "conv_test_reentrant"

    async with conversation_mutation_lock(conv_id):
        assert holds_conversation_mutation_lock(conv_id) is True
        async with conversation_mutation_lock(conv_id):
            assert holds_conversation_mutation_lock(conv_id) is True

    assert holds_conversation_mutation_lock(conv_id) is False


async def test_lock_holder_does_not_leak_into_background_task():
    """A background task spawned while the turn holds the lock must NOT be seen
    as holding it. asyncio.create_task copies the contextvar snapshot, so a
    string-only holder would make the child's reentrancy check return True and
    let it mutate the chain with no lock. The guard is task-aware, so the child
    (a different task) reports False. AUDIT-memory HIGH (C8)."""
    conv_id = "conv_bg_leak"
    result: dict = {}

    async with conversation_mutation_lock(conv_id):
        assert holds_conversation_mutation_lock(conv_id) is True

        async def _bg() -> None:
            # Inherits the parent's contextvar snapshot but is a distinct task.
            result["held"] = holds_conversation_mutation_lock(conv_id)

        await asyncio.create_task(_bg())

    assert result["held"] is False


async def test_background_task_acquires_lock_not_reentrant():
    """Because the inherited holder does not satisfy the guard, a background
    task actually contends for the lock instead of short-circuiting — it can
    only enter after the turn releases."""
    conv_id = "conv_bg_contend"
    events: list[str] = []
    bg_entered = asyncio.Event()

    async def _bg() -> None:
        async with conversation_mutation_lock(conv_id):
            events.append("bg_enter")
            bg_entered.set()

    async with conversation_mutation_lock(conv_id):
        events.append("turn_hold")
        task = asyncio.create_task(_bg())
        # Give the background task a chance to run; it must NOT enter yet.
        await asyncio.sleep(0.05)
        assert not bg_entered.is_set(), events
        events.append("turn_release")

    await task
    # The background task entered only after the turn released.
    assert events == ["turn_hold", "turn_release", "bg_enter"]


async def test_add_interaction_skips_nested_lock_when_turn_lock_held():
    from jvagent.memory.conversation import Conversation

    conv = MagicMock(spec=Conversation)
    conv.id = "conv_nested"
    conv._add_interaction_unlocked = AsyncMock(return_value="interaction")

    with patch(
        "jvagent.memory.distributed_conversation_lock.holds_conversation_mutation_lock",
        return_value=True,
    ):
        with patch(
            "jvagent.memory.distributed_conversation_lock.conversation_mutation_lock"
        ) as lock_cm:
            result = await Conversation.add_interaction(
                conv, utterance="hello", session_id="s1"
            )

    assert result == "interaction"
    conv._add_interaction_unlocked.assert_awaited_once()
    lock_cm.assert_not_called()


async def test_memory_lock_serializes_concurrent_turns():
    """Two coroutines contending on the same conversation id run sequentially."""
    conv_id = "conv_serialize"
    order: list[str] = []

    async def worker(name: str) -> None:
        async with conversation_mutation_lock(conv_id):
            order.append(f"{name}_start")
            await asyncio.sleep(0.05)
            order.append(f"{name}_end")

    await asyncio.gather(worker("a"), worker("b"))

    # One worker must fully finish before the other starts.
    assert order.index("a_end") < order.index("b_start") or order.index(
        "b_end"
    ) < order.index("a_start")
