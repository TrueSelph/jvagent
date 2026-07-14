"""Characterization tests for Conversation.get_interactions ordering/limits.

Written before batch-loading the implementation (previously a node-by-node
chain walk = N sequential DB fetches per turn on the history hot path).
"""

import uuid

from jvagent.memory.conversation import Conversation


async def _conv_with(n):
    conv = await Conversation.create(
        session_id=f"gi-{uuid.uuid4().hex[:10]}",
        user_id="u1",
        channel="default",
    )
    for i in range(n):
        await conv.add_interaction(utterance=f"msg {i}")
    return conv


async def test_chronological_order_and_content(test_db):
    conv = await _conv_with(5)
    try:
        out = await conv.get_interactions()
        assert [i.utterance for i in out] == [f"msg {i}" for i in range(5)]
    finally:
        await conv.delete(cascade=True)


async def test_forward_limit_returns_oldest_n(test_db):
    conv = await _conv_with(5)
    try:
        out = await conv.get_interactions(limit=2)
        assert [i.utterance for i in out] == ["msg 0", "msg 1"]
    finally:
        await conv.delete(cascade=True)


async def test_reverse_limit_returns_newest_n_newest_first(test_db):
    conv = await _conv_with(5)
    try:
        out = await conv.get_interactions(limit=2, reverse=True)
        assert [i.utterance for i in out] == ["msg 4", "msg 3"]
    finally:
        await conv.delete(cascade=True)


async def test_empty_conversation_returns_empty(test_db):
    conv = await Conversation.create(
        session_id=f"gi-{uuid.uuid4().hex[:10]}", user_id="u1", channel="default"
    )
    try:
        assert await conv.get_interactions() == []
        assert await conv.get_interactions(reverse=True) == []
    finally:
        await conv.delete(cascade=True)
