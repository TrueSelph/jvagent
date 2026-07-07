"""Tests for Memory counter accuracy.

Covers:
- total_users / total_conversations after purge operations
- Concurrent conversation deletes do not corrupt the counter
- repair_memory (_recalculate_counters) corrects drift
- interaction_count reconciliation via _recalculate_counters
- dual-branch sequential chaining in repair_memory
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jvspatial.core import get_default_context

from jvagent.core.graph_repair_handlers import _reattach_user
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
    user = await User.create(
        memory_id=memory.id,
        user_id=user_id or _uid(),
    )
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
    await _make_user(memory)
    await _make_user(memory)
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
# orphaned interaction cleanup safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_orphaned_interactions_preserves_existing_foreign_conversation(
    test_db,
):
    """Do not delete interactions when their conversation node still exists."""
    memory_a = await _setup_memory()
    memory_b = await _setup_memory()
    user_b = await _make_user(memory_b)
    conv_b = await _make_conversation(memory_b, user_b)
    interaction = await Interaction.create(
        conversation_id=conv_b.id,
        user_id=user_b.user_id,
        utterance="keep me",
        channel=conv_b.channel,
        session_id=conv_b.session_id,
    )
    await conv_b.connect(interaction)

    deleted = await memory_a._cleanup_orphaned_interactions()

    assert deleted == 0
    assert await Interaction.get(interaction.id) is not None


@pytest.mark.asyncio
async def test_cleanup_orphaned_interactions_deletes_missing_conversation(test_db):
    """Delete interactions only when their referenced conversation is missing."""
    memory = await _setup_memory()
    interaction = await Interaction.create(
        conversation_id=f"n.Conversation.missing_{uuid.uuid4().hex[:8]}",
        user_id=_uid(),
        utterance="orphaned",
        channel="default",
        session_id=_sid(),
    )

    deleted = await memory._cleanup_orphaned_interactions()

    assert deleted == 1
    assert await Interaction.get(interaction.id) is None


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


# ---------------------------------------------------------------------------
# Dual-branch sequential chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_memory_chains_dual_branch_interactions(test_db):
    """repair_memory chains second branched node to first instead of orphaning."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    conv = await _make_conversation(memory, user)

    # Build normal chain: Conv -> I1 -> I2
    i1 = await conv.add_interaction(utterance="first")
    i2 = await conv.add_interaction(utterance="second")

    # Create I3 and connect I1 -> I3 to form dual branch (I1 -> I2 and I1 -> I3)
    # Use i1.started_at as base so timezone matches (avoids naive/aware comparison)
    base = i1.started_at or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    i3 = await Interaction.create(
        conversation_id=conv.id,
        user_id=conv.user_id,
        utterance="third",
        channel=conv.channel,
        session_id=conv.session_id,
        started_at=base + timedelta(seconds=2),
    )
    await i1.connect(i3, direction="both")

    # Verify dual branch exists: I1 has two outgoing
    next_from_i1 = await i1.nodes(node=Interaction, direction="out")
    assert len(next_from_i1) == 2

    result = await memory.repair_memory()
    assert result["dual_edges_removed"] >= 1

    # Verify sequential chain: I1 -> I2 -> I3
    next_i1 = await i1.get_next_interaction()
    assert next_i1 is not None
    assert next_i1.id == i2.id
    next_i2 = await i2.get_next_interaction()
    assert next_i2 is not None
    assert next_i2.id == i3.id
    next_i3 = await i3.get_next_interaction()
    assert next_i3 is None


# ---------------------------------------------------------------------------
# Conversation-branch sequential chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_memory_chains_conversation_branch_interactions(test_db):
    """repair_memory chains second branch from conversation into first chain."""
    memory = await _setup_memory()
    user = await _make_user(memory)
    conv = await _make_conversation(memory, user)

    # Build normal chain: Conv -> I1 -> I2
    i1 = await conv.add_interaction(utterance="first")
    i2 = await conv.add_interaction(utterance="second")

    # Create I3 and connect Conv -> I3 to form conversation branch (Conv -> I1 and Conv -> I3)
    base = i2.started_at or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    i3 = await Interaction.create(
        conversation_id=conv.id,
        user_id=conv.user_id,
        utterance="third",
        channel=conv.channel,
        session_id=conv.session_id,
        started_at=base + timedelta(seconds=1),
    )
    await conv.connect(i3, direction="out")

    # Verify conversation branch exists: Conv has two outgoing to interactions
    conv_out = await conv.nodes(node=Interaction, direction="out")
    assert len(conv_out) == 2

    result = await memory.repair_memory()
    assert result["conversation_branch_edges_removed"] >= 1

    # Verify sequential chain: Conv -> I1 -> I2 -> I3
    conv_out_after = await conv.nodes(node=Interaction, direction="out")
    assert len(conv_out_after) == 1
    assert conv_out_after[0].id == i1.id

    next_i1 = await i1.get_next_interaction()
    assert next_i1 is not None
    assert next_i1.id == i2.id
    next_i2 = await i2.get_next_interaction()
    assert next_i2 is not None
    assert next_i2.id == i3.id
    next_i3 = await i3.get_next_interaction()
    assert next_i3 is None


# ---------------------------------------------------------------------------
# total_users writers: reattach user, purge instance sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reattach_user_increments_memory_total_users(test_db):
    """Graph repair user reattach must increment total_users like get_user create."""
    memory = await Memory.create()
    memory.total_users = 0
    await memory.save()
    u = await User.create(memory_id="", user_id=f"orphan_{uuid.uuid4().hex[:8]}")
    ctx = get_default_context()
    orphan_ids: set = set()
    ok = await _reattach_user(ctx, u, orphan_ids, dry_run=False)
    assert ok is True
    fresh = await Memory.get(memory.id)
    assert fresh is not None
    assert len(await fresh.nodes(node=User)) == 1
    assert fresh.total_users == 1


@pytest.mark.asyncio
async def test_purge_user_memory_syncs_instance_counters_from_db(test_db):
    """After purge, the Memory instance should match persisted counters."""
    memory = await _setup_memory()
    await _make_user(memory)
    await _make_user(memory)
    assert memory.total_users == 2

    await memory.purge_user_memory()

    assert memory.total_users == 0
    users_left = await memory.nodes(node=User)
    assert len(users_left) == 0
