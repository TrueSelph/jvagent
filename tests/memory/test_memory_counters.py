"""Tests for Memory counter accuracy.

Covers:
- total_users / total_conversations after purge operations
- Concurrent conversation deletes do not corrupt the counter
- repair_memory (_recalculate_counters) corrects drift
- interaction_count reconciliation via _recalculate_counters
"""

import asyncio
import uuid

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.manager import Memory
from jvagent.memory.user import User


def _uid():
    return f"user_{uuid.uuid4().hex[:12]}"


def _sid():
    return f"sess_{uuid.uuid4().hex[:12]}"


async def _setup_memory() -> Memory:
    """Create a bare Memory node for testing."""
    return await Memory.create()


async def _make_user(memory: Memory, user_id: str | None = None) -> User:
    user = await User.create(user_id=user_id or _uid())
    await memory.connect(user)
    memory.total_users += 1
    await memory.save()
    return user


async def _make_conversation(memory: Memory, user: User) -> Conversation:
    conv = await Conversation.create(
        session_id=_sid(),
        user_id=user.user_id,
        channel="default",
    )
    await user.connect(conv)
    memory.total_conversations += 1
    await memory.save()
    return conv


# ---------------------------------------------------------------------------
# Counter accuracy after purge_user_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_users_zero_after_purge_all(test_db):
    """purge_user_memory should bring total_users to 0."""
    memory = await _setup_memory()
    u1 = await _make_user(memory)
    u2 = await _make_user(memory)
    assert memory.total_users == 2

    await memory.purge_user_memory()

    # Reload from DB to verify persisted value
    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_users == 0


@pytest.mark.asyncio
async def test_total_conversations_zero_after_purge_user(test_db):
    """purge_user_memory should decrement total_conversations for deleted conversations."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    await _make_conversation(memory, user)
    await _make_conversation(memory, user)
    assert memory.total_conversations == 2

    await memory.purge_user_memory(user_id=user.user_id)

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_conversations == 0


# ---------------------------------------------------------------------------
# Counter accuracy after purge_conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_conversations_zero_after_purge_conversations(test_db):
    """purge_conversations should decrement total_conversations correctly."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    await _make_conversation(memory, user)
    await _make_conversation(memory, user)
    assert memory.total_conversations == 2

    await memory.purge_conversations(user_id=user.user_id)

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_conversations == 0


@pytest.mark.asyncio
async def test_total_users_unaffected_after_purge_conversations(test_db):
    """purge_conversations must not alter total_users."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    await _make_conversation(memory, user)

    await memory.purge_conversations(user_id=user.user_id)

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_users == 1


# ---------------------------------------------------------------------------
# Concurrent conversation deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_conversation_deletes_accurate_counter(test_db):
    """Deleting multiple conversations concurrently should not lose decrements."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    n = 5
    convs = [await _make_conversation(memory, user) for _ in range(n)]
    assert memory.total_conversations == n

    await asyncio.gather(*(c.delete(cascade=True) for c in convs))

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_conversations == 0


# ---------------------------------------------------------------------------
# _recalculate_counters repairs drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalculate_counters_repairs_total_users_drift(test_db):
    """_recalculate_counters corrects a total_users value that does not match the graph."""
    memory = await _setup_memory()
    await _make_user(memory)
    await _make_user(memory)

    # Introduce artificial drift
    memory.total_users = 99
    await memory.save()

    fixed = await memory._recalculate_counters()
    assert fixed >= 1

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_users == 2


@pytest.mark.asyncio
async def test_recalculate_counters_repairs_total_conversations_drift(test_db):
    """_recalculate_counters corrects a drifted total_conversations."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    await _make_conversation(memory, user)
    await _make_conversation(memory, user)

    # Introduce artificial drift
    memory.total_conversations = 0
    await memory.save()

    fixed = await memory._recalculate_counters()
    assert fixed >= 1

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_conversations == 2


# ---------------------------------------------------------------------------
# interaction_count reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalculate_counters_repairs_interaction_count(test_db):
    """_recalculate_counters corrects interaction_count when it has drifted."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    conv = await _make_conversation(memory, user)

    # Add interactions via the proper path so the chain is valid
    await conv.add_interaction(utterance="hello")
    await conv.add_interaction(utterance="world")
    assert conv.interaction_count == 2

    # Introduce drift directly on the object and persist it
    conv.interaction_count = 99
    await conv.save()

    fixed = await memory._recalculate_counters()
    assert fixed >= 1

    fresh_conv = await Conversation.get(conv.id)
    assert fresh_conv is not None
    assert fresh_conv.interaction_count == 2


@pytest.mark.asyncio
async def test_recalculate_counters_returns_zero_when_accurate(test_db):
    """_recalculate_counters returns 0 when nothing needs correction."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    conv = await _make_conversation(memory, user)
    await conv.add_interaction(utterance="test")

    fixed = await memory._recalculate_counters()
    assert fixed == 0


# ---------------------------------------------------------------------------
# repair_memory flow (end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_memory_restores_counters_from_graph(test_db):
    """repair_memory corrects both total_users and total_conversations from ground truth."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    await _make_conversation(memory, user)

    # Corrupt both counters
    memory.total_users = 42
    memory.total_conversations = 42
    await memory.save()

    result = await memory.repair_memory()
    assert result["counters_fixed"] >= 2

    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert fresh.total_users == 1
    assert fresh.total_conversations == 1
