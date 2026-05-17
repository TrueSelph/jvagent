"""Tests for cross-memory purge defense (AUDIT-memory CRIT-03).

``Memory.purge_conversations(conversation_id=X)`` must refuse to delete a
conversation whose owning User is not connected to ``self`` (the calling
Memory node). Without this check, any admin could delete conversations
across tenants by supplying any conversation_id.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.memory.manager import Memory


class _FakeConversation:
    def __init__(self, owner_id: str, conv_id: str = "conv_x"):
        self.id = conv_id
        self._owner = SimpleNamespace(id=owner_id)
        self.delete = AsyncMock()

    async def nodes(self, node=None, direction=None):  # noqa: ARG002
        return [self._owner]


@pytest.mark.asyncio
async def test_purge_conversation_id_belongs_to_other_memory_refused():
    """If conversation owner User is not in this Memory's user set, refuse."""
    memory = Memory()

    in_scope = SimpleNamespace(id="user_in_memory")
    other_owners_conv = _FakeConversation(owner_id="user_in_other_memory")

    with patch.object(
        Memory,
        "users_scoped_to_this_memory",
        new=AsyncMock(return_value=[in_scope]),
    ), patch(
        "jvagent.memory.conversation.Conversation.get",
        new=AsyncMock(return_value=other_owners_conv),
    ):
        result = await memory.purge_conversations(conversation_id="conv_x")

    assert result is None
    other_owners_conv.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_conversation_id_belongs_to_this_memory_succeeds():
    memory = Memory()
    in_scope = SimpleNamespace(id="user_in_memory")
    legit_conv = _FakeConversation(owner_id="user_in_memory")

    with patch.object(
        Memory,
        "users_scoped_to_this_memory",
        new=AsyncMock(return_value=[in_scope]),
    ), patch(
        "jvagent.memory.conversation.Conversation.get",
        new=AsyncMock(return_value=legit_conv),
    ):
        result = await memory.purge_conversations(conversation_id="conv_x")

    assert result == [legit_conv]
    legit_conv.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_missing_conversation_returns_none():
    memory = Memory()
    with patch.object(
        Memory,
        "users_scoped_to_this_memory",
        new=AsyncMock(return_value=[]),
    ), patch(
        "jvagent.memory.conversation.Conversation.get",
        new=AsyncMock(return_value=None),
    ):
        result = await memory.purge_conversations(conversation_id="missing")
    assert result is None
