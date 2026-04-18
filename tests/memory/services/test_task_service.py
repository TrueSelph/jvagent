"""Tests for shared TaskService lifecycle operations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.services.task_service import TaskService


def _conversation_stub():
    conversation = MagicMock()
    conversation.active_tasks = []
    conversation.save = AsyncMock()
    return conversation


@pytest.mark.asyncio
async def test_start_creates_task_with_promoted_trigger_fields():
    conversation = _conversation_stub()
    svc = TaskService(conversation)

    handle = await svc.start(
        description="Follow up later",
        task_type="PROACTIVE",
        metadata={"context": "ctx"},
        trigger_at="2026-04-18T10:30",
        trigger_condition="checkin",
    )

    assert handle.task_id
    assert len(conversation.active_tasks) == 1
    task = conversation.active_tasks[0]
    assert task["status"] == "active"
    assert task["next_trigger_at"] == "2026-04-18T10:30"
    assert task["trigger_condition"] == "checkin"
    assert task["metadata"]["context"] == "ctx"


@pytest.mark.asyncio
async def test_singleton_action_supersedes_previous_active_task():
    conversation = _conversation_stub()
    svc = TaskService(conversation)

    first = await svc.start(
        description="Task A",
        task_type="AGENTIC_LOOP",
        action_name="MyAction",
        singleton_action=True,
    )
    second = await svc.start(
        description="Task A",
        task_type="AGENTIC_LOOP",
        action_name="MyAction",
        metadata={"state": "updated"},
        singleton_action=True,
    )

    assert len(conversation.active_tasks) == 2
    statuses = {t["task_id"]: t["status"] for t in conversation.active_tasks}
    assert statuses[first.task_id] == "superseded"
    assert statuses[second.task_id] == "active"
    assert (
        next(t for t in conversation.active_tasks if t["task_id"] == second.task_id)[
            "metadata"
        ]["state"]
        == "updated"
    )


@pytest.mark.asyncio
async def test_explicit_task_id_updates_existing_entry_in_place():
    conversation = _conversation_stub()
    svc = TaskService(conversation)

    await svc.start(
        description="Task A",
        task_type="AGENTIC_LOOP",
        task_id="explicit-id",
    )
    await svc.start(
        description="Task A (revised)",
        task_type="AGENTIC_LOOP",
        task_id="explicit-id",
        metadata={"state": "updated"},
    )

    assert len(conversation.active_tasks) == 1
    entry = conversation.active_tasks[0]
    assert entry["task_id"] == "explicit-id"
    assert entry["description"] == "Task A (revised)"
    assert entry["metadata"]["state"] == "updated"


@pytest.mark.asyncio
async def test_record_step_tracks_iterations_and_tool_usage():
    conversation = _conversation_stub()
    svc = TaskService(conversation)
    task = await svc.start(description="Run", task_type="AGENTIC_LOOP")

    await task.record_step("thinking", iteration=1, details={"tokens": 42})
    await task.record_step("tool_call", iteration=1, details={"tool": "read_file"})

    metadata = svc.get(task_id=task.task_id)["metadata"]
    assert metadata["iterations"] == 1
    assert metadata["thinking_tokens_used"] == 42
    assert "read_file" in metadata["tools_called"]
    assert len(metadata["steps"]) == 2


@pytest.mark.asyncio
async def test_complete_sets_terminal_metadata():
    conversation = _conversation_stub()
    svc = TaskService(conversation)
    task = await svc.start(
        description="Run",
        task_type="AGENTIC_LOOP",
        metadata={"started_at": "2026-04-18T00:00:00+00:00"},
    )

    ok = await task.complete(summary="done")
    assert ok is True
    updated = svc.get(task_id=task.task_id)
    assert updated["status"] == "completed"
    assert updated["terminal_at"] is not None
    assert updated["metadata"]["final_summary"] == "done"
    assert updated["metadata"]["completed_at"] is not None


@pytest.mark.asyncio
async def test_reserve_only_succeeds_for_active():
    conversation = _conversation_stub()
    svc = TaskService(conversation)
    task = await svc.start(description="Dispatch me", task_type="PROACTIVE")

    assert await svc.reserve(task.task_id) is True
    assert svc.get(task_id=task.task_id)["status"] == "reserved"
    assert await svc.reserve(task.task_id) is False


@pytest.mark.asyncio
async def test_track_context_marks_failed_on_exception():
    conversation = _conversation_stub()
    svc = TaskService(conversation)

    with pytest.raises(RuntimeError):
        async with svc.track(description="Tracked", task_type="AGENTIC_LOOP") as handle:
            assert handle.task_id
            raise RuntimeError("boom")

    assert len(conversation.active_tasks) == 1
    task = conversation.active_tasks[0]
    assert task["status"] == "failed"
    assert task["metadata"]["failure_reason"] == "boom"
