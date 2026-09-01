"""Regression tests for AUDIT-memory HIGH findings (2026-09-01)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.distributed_conversation_lock import conversation_mutation_lock
from jvagent.memory.task_store import Task, TaskStore


def _sid(prefix: str = "high") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class FakeConversation:
    def __init__(self, conv_id: str = "conv-1") -> None:
        self.id = conv_id
        self.tasks: List[Dict[str, Any]] = []

    async def save(self) -> None:
        pass


@pytest.mark.asyncio
async def test_add_interaction_unlocked_rereads_under_lock(test_db):
    """Under conversation_mutation_lock, _add_interaction_unlocked re-fetches the row."""
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        fresh = await Conversation.get(conv.id)
        assert fresh is not None

        with patch.object(
            Conversation,
            "get",
            new=AsyncMock(return_value=fresh),
        ) as get_mock:
            async with conversation_mutation_lock(conv.id):
                interaction = await conv._add_interaction_unlocked(
                    utterance="hello",
                    session_id="",
                )

        get_mock.assert_awaited()
        assert interaction.utterance == "hello"
        assert conv.interaction_count == 1
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_prune_connects_new_head_before_disconnect(test_db):
    """Rewiring the chain must connect the successor before dropping the head edge."""
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    conv.interaction_limit = 0  # disable auto-prune while building the chain
    try:
        i1 = await conv.add_interaction(utterance="m1")
        i2 = await conv.add_interaction(utterance="m2")

        order: list[str] = []
        real_connect = Conversation.connect
        real_disconnect = Conversation.disconnect

        async def connect_wrapper(self, target, direction="both"):
            order.append(f"connect:{target.id}")
            return await real_connect(self, target, direction=direction)

        async def disconnect_wrapper(self, target):
            order.append(f"disconnect:{target.id}")
            return await real_disconnect(self, target)

        conv.interaction_limit = 1
        conv.interaction_count = 2
        with patch.object(Conversation, "connect", connect_wrapper), patch.object(
            Conversation, "disconnect", disconnect_wrapper
        ):
            await conv._prune_old_interactions()

        connect_i2 = f"connect:{i2.id}"
        disconnect_i1 = f"disconnect:{i1.id}"
        assert connect_i2 in order
        assert disconnect_i1 in order
        assert order.index(connect_i2) < order.index(disconnect_i1)
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_reap_artifacts_reconnects_registry_on_delete_failure(test_db):
    """Failed artifact.delete() must not leave the registry edge dropped."""
    from jvagent.memory.artifact import Artifact, Artifacts

    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        i1 = await conv.add_interaction(utterance="m1")
        art = await conv.add_artifact(i1, name="solo", source="vision", data="d")
        branch = (await conv.nodes(node=Artifacts, direction="out"))[0]

        with patch.object(
            Artifact,
            "delete",
            new=AsyncMock(side_effect=RuntimeError("delete failed")),
        ):
            reaped = await conv._reap_artifacts_for(i1)

        assert reaped == 0
        assert await branch.is_connected_to(art)
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_get_or_create_artifacts_serializes_under_lock(test_db):
    """Concurrent lazy branch creation must yield a single Artifacts registry."""
    from jvagent.memory.artifact import Artifacts

    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        import asyncio

        async def _create():
            return await conv._get_or_create_artifacts()

        branches = await asyncio.gather(_create(), _create(), _create())
        assert len({id(b) for b in branches}) == 1
        out = await conv.nodes(node=Artifacts, direction="out")
        assert len(out) == 1
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_get_users_propagates_backend_errors(monkeypatch):
    """Admin list-users must not mask backend failures as an empty 200."""
    from jvagent.memory.endpoints import get_users

    class _FakeMemory:
        async def get_users(self):
            raise RuntimeError("db unavailable")

    class _FakeAgent:
        async def get_memory(self):
            return _FakeMemory()

    async def _fake_get(_agent_id: str):  # noqa: ARG001
        return _FakeAgent()

    from jvagent.memory import endpoints as ep

    monkeypatch.setattr(ep.Agent, "get", staticmethod(_fake_get))

    with pytest.raises(RuntimeError, match="db unavailable"):
        await get_users(agent_id="agent_x", filter=None, page=1, page_size=50)


@pytest.mark.asyncio
async def test_persist_task_appends_when_id_missing(caplog):
    """Terminal transitions must not no-op when the task row is missing."""
    conv = FakeConversation()
    store = TaskStore(conv)  # type: ignore[arg-type]
    task = Task(
        id="task_missing12345",
        title="t",
        description="t",
        status="active",
        owner_action="O",
    )
    with caplog.at_level(logging.WARNING):
        await store._persist_task(task)
    assert len(conv.tasks) == 1
    assert conv.tasks[0]["id"] == "task_missing12345"
    assert any("appending" in r.message.lower() for r in caplog.records)
