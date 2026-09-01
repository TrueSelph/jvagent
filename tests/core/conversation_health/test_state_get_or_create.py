"""ConversationHealthState creation must serialize under a per-agent lock."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core.conversation_health.state import ConversationHealthState

pytestmark = pytest.mark.asyncio


async def test_get_or_create_acquires_per_agent_lock():
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    lock_mgr = AsyncMock()
    lock_mgr.acquire = AsyncMock(return_value=lock)

    existing = ConversationHealthState(agent_id="agent-1")
    with (
        patch(
            "jvagent.memory.lock_manager.get_user_lock_manager",
            return_value=lock_mgr,
        ),
        patch.object(
            ConversationHealthState,
            "find_one",
            AsyncMock(return_value=existing),
        ),
    ):
        result = await ConversationHealthState.get_or_create_for_agent("agent-1")

    assert result is existing
    lock_mgr.acquire.assert_awaited_once_with("conversation-health-state:agent-1")
    lock.__aenter__.assert_awaited_once()
