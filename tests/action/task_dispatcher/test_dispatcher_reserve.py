"""Reserve-path tests aligned with TaskDispatcher semantics."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_store import TaskStore


def _conversation_stub():
    conversation = MagicMock()
    conversation.tasks = []
    conversation.save = AsyncMock()
    return conversation


@pytest.mark.asyncio
async def test_reserve_then_complete_mimics_dispatch_success():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Dispatch me", description="Dispatch me")
    await handle.start()

    await handle.complete(result="dispatched")
    assert handle.status == "completed"


@pytest.mark.asyncio
async def test_reserve_then_fail_mimics_dispatch_error():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Dispatch me", description="Dispatch me")
    await handle.start()

    await handle.fail(reason="dispatcher boom")
    assert handle.status == "failed"
    assert handle.data.get("failure_reason") == "dispatcher boom"
