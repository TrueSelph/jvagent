"""Concurrent same-session get_session must yield ONE Conversation (ADR-0033, C6).

get_session did check-then-create for the Conversation with no per-session lock,
so two concurrent first messages for the same session_id both missed the
existence check and each created a Conversation — forking history and giving the
copies different token_secrets. get_session now serializes on a per-session lock,
mirroring get_user."""

from __future__ import annotations

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.manager import Memory
from tests._concurrency import run_concurrent

pytestmark = pytest.mark.asyncio


async def test_concurrent_same_session_single_conversation(test_db):
    memory = await Memory.create()
    sid = "shared-session-1"

    results = await run_concurrent(lambda i: memory.get_session(session_id=sid), n=8)

    # No coroutine failed.
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, errors

    # Every call resolved to the SAME conversation.
    conv_ids = {r[1].id for r in results}
    assert len(conv_ids) == 1

    # Exactly one Conversation persisted for this session_id.
    convs = await Conversation.find({"context.session_id": sid})
    assert len(convs) == 1
    assert conv_ids == {convs[0].id}


async def test_distinct_sessions_get_distinct_conversations(test_db):
    memory = await Memory.create()

    results = await run_concurrent(
        lambda i: memory.get_session(session_id=f"sess-{i}"), n=5
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, errors

    conv_ids = {r[1].id for r in results}
    assert len(conv_ids) == 5  # one per distinct session
