"""Multi-agent memory isolation: distinct User nodes per Memory for same external user_id."""

import pytest

from jvagent.memory.manager import Memory
from jvagent.memory.user import User


@pytest.mark.asyncio
async def test_same_external_user_id_two_memories_distinct_users(test_db):
    """Two Memory roots each get their own User document for the same user_id."""
    m1 = await Memory.create()
    m2 = await Memory.create()

    u1 = await m1.get_user("alice", create_if_missing=True)
    u2 = await m2.get_user("alice", create_if_missing=True)

    assert u1 is not None and u2 is not None
    assert u1.id != u2.id
    assert u1.user_id == "alice" == u2.user_id
    assert u1.memory_id == m1.id
    assert u2.memory_id == m2.id


@pytest.mark.asyncio
async def test_conversations_not_shared_across_memories(test_db):
    """Conversations under one memory's user are invisible to the other memory."""
    m1 = await Memory.create()
    m2 = await Memory.create()

    u1 = await m1.get_user("bob", create_if_missing=True)
    u2 = await m2.get_user("bob", create_if_missing=True)

    c1 = await u1.create_conversation(channel="default")
    c2 = await u2.create_conversation(channel="default")

    assert c1.id != c2.id

    found_m1 = await m1.get_conversation_by_session(c1.session_id)
    found_m2_other = await m2.get_conversation_by_session(c1.session_id)
    assert found_m1 is not None
    assert found_m2_other is None

    found_m2 = await m2.get_conversation_by_session(c2.session_id)
    found_m1_other = await m1.get_conversation_by_session(c2.session_id)
    assert found_m2 is not None
    assert found_m1_other is None


@pytest.mark.asyncio
async def test_user_list_conversations_uses_graph_only(test_db):
    """list_conversations returns only edges from this User node."""
    m1 = await Memory.create()
    m2 = await Memory.create()
    u1 = await m1.get_user("carol", create_if_missing=True)
    u2 = await m2.get_user("carol", create_if_missing=True)
    await u1.create_conversation()
    await u2.create_conversation()
    await u2.create_conversation()

    assert len(await u1.list_conversations()) == 1
    assert len(await u2.list_conversations()) == 2


@pytest.mark.asyncio
async def test_get_session_scoped_to_memory(test_db):
    """session_id from another memory does not resolve in get_session."""
    m1 = await Memory.create()
    m2 = await Memory.create()
    u2 = await m2.get_user("dave", create_if_missing=True)
    c2 = await u2.create_conversation(channel="default")

    with pytest.raises(ValueError, match="not accessible from this agent"):
        await m1.get_session(user_id=None, session_id=c2.session_id, channel="default")


@pytest.mark.asyncio
async def test_users_counted_per_memory_id_not_stale_edges(test_db):
    """Listing/export stay memory_id-scoped; total_users follows edge count(node=User)."""
    m_a = await Memory.create()
    m_b = await Memory.create()
    u = await User.create(memory_id=m_b.id, user_id="stale_edge_user")
    await m_a.connect(u)

    assert await m_a.get_users() == []
    assert (await m_a.memory_healthcheck())["total_users"] == 1
    await m_a._recalculate_counters()
    assert m_a.total_users == 1
    assert (await m_a.export_memory())["users"] == []


@pytest.mark.asyncio
async def test_legacy_empty_memory_id_still_listed_under_connected_memory(test_db):
    """Legacy User with empty memory_id remains visible on the Memory it connects to."""
    memory = await Memory.create()
    u = await User.create(memory_id="", user_id="legacy_no_scope")
    await memory.connect(u)

    listed = await memory.get_users()
    assert len(listed) == 1
    assert listed[0].id == u.id


@pytest.mark.asyncio
async def test_count_node_user_matches_outgoing_edges(test_db):
    """count_neighbors(node=User) matches len(nodes(node=User)) for this Memory."""
    from jvagent.memory.user import User

    m = await Memory.create()
    u1 = await m.get_user("count_parity_a", create_if_missing=True)
    u2 = await m.get_user("count_parity_b", create_if_missing=True)
    assert await m.count_neighbors(node=User) == len(await m.nodes(node=User)) == 2
    assert u1.id != u2.id
