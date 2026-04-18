"""Reserve-path tests aligned with TaskDispatcher semantics."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.services.task_service import TaskService


def _conversation_stub():
    conversation = MagicMock()
    conversation.active_tasks = []
    conversation.save = AsyncMock()
    return conversation


@pytest.mark.asyncio
async def test_reserve_then_complete_mimics_dispatch_success():
    conversation = _conversation_stub()
    svc = TaskService(conversation)
    task = await svc.start(description="Dispatch me", task_type="PROACTIVE")

    assert await svc.reserve(task.task_id) is True
    assert svc.get(task_id=task.task_id)["status"] == "reserved"

    await svc.complete(task.task_id, status="completed", summary="dispatched")
    assert svc.get(task_id=task.task_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_reserve_then_fail_mimics_dispatch_error():
    conversation = _conversation_stub()
    svc = TaskService(conversation)
    task = await svc.start(description="Dispatch me", task_type="PROACTIVE")

    assert await svc.reserve(task.task_id) is True
    await svc.fail(task.task_id, error="dispatcher boom")
    updated = svc.get(task_id=task.task_id)
    assert updated["status"] == "failed"
    assert updated["metadata"]["failure_reason"] == "dispatcher boom"
