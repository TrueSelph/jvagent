"""export_memory / memory_healthcheck must walk the full interaction chain
(AUDIT-memory HIGH).

A Conversation is edge-connected only to the chain head, so
conv.nodes(node=Interaction) (direction='out') returns exactly one interaction.
The backup export and the healthcheck count must instead use
get_interactions(limit=0), which batch-loads the whole chain by
conversation_id — otherwise every conversation reports ~1 interaction and the
export silently drops the rest."""

from __future__ import annotations

import pytest

from jvagent.memory.manager import Memory

pytestmark = pytest.mark.asyncio


async def _seed(memory, user_id, session_id, n):
    user = await memory.get_user(user_id)
    conv = await user.create_conversation(session_id=session_id, channel="default")
    for i in range(n):
        await conv.add_interaction(utterance=f"m{i}")
    return conv


async def test_healthcheck_counts_full_chain(test_db):
    memory = await Memory.create()
    await _seed(memory, "u1", "s1", 4)

    stats = await memory.memory_healthcheck()

    assert stats["total_conversations"] == 1
    assert stats["total_interactions"] == 4


async def test_export_includes_all_interactions(test_db):
    memory = await Memory.create()
    await _seed(memory, "u1", "s1", 4)

    export = await memory.export_memory()

    convs = export["users"][0]["conversations"]
    total = sum(len(c["interactions"]) for c in convs)
    assert total == 4


async def test_healthcheck_multi_conversation(test_db):
    memory = await Memory.create()
    await _seed(memory, "u1", "s1", 3)
    await _seed(memory, "u2", "s2", 2)

    stats = await memory.memory_healthcheck()

    assert stats["total_conversations"] == 2
    assert stats["total_interactions"] == 5
