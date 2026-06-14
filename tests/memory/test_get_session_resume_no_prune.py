"""get_session resume paths must not synchronously prune (startup latency)."""

from unittest.mock import AsyncMock

import pytest

from jvagent.memory.manager import Memory


@pytest.mark.asyncio
async def test_get_session_session_only_does_not_call_ensure_limit(test_db):
    memory = await Memory.create()
    user = await memory.get_user("resume_user", create_if_missing=True)
    conv = await user.create_conversation(channel="default")

    memory._ensure_conversation_interaction_limit = AsyncMock(return_value=0)  # type: ignore[method-assign]

    await memory.get_session(
        user_id=None,
        session_id=conv.session_id,
        channel="default",
    )
    memory._ensure_conversation_interaction_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_session_both_ids_does_not_call_ensure_limit(test_db):
    memory = await Memory.create()
    user = await memory.get_user("both_ids_user", create_if_missing=True)
    conv = await user.create_conversation(channel="default")

    memory._ensure_conversation_interaction_limit = AsyncMock(return_value=0)  # type: ignore[method-assign]

    await memory.get_session(
        user_id=user.user_id,
        session_id=conv.session_id,
        channel="default",
    )
    memory._ensure_conversation_interaction_limit.assert_not_awaited()
