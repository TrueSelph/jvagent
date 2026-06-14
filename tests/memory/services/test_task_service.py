"""Tests for TaskStore lifecycle operations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_store import TaskStore


def _conversation_stub():
    conversation = MagicMock()
    conversation.tasks = []
    conversation.save = AsyncMock()
    return conversation


@pytest.mark.asyncio
async def test_start_creates_task_with_data():
    conversation = _conversation_stub()
    store = TaskStore(conversation)

    handle = await store.create(
        title="Follow up later",
        description="Follow up later",
        data={
            "context": "ctx",
            "trigger_at": "2026-04-18T10:30",
            "trigger_condition": "checkin",
        },
    )
    await handle.start()

    assert handle.id
    assert len(conversation.tasks) == 1
    task = conversation.tasks[0]
    assert task["status"] == "active"
    assert task["data"]["trigger_at"] == "2026-04-18T10:30"
    assert task["data"]["trigger_condition"] == "checkin"
    assert task["data"]["context"] == "ctx"


@pytest.mark.asyncio
async def test_explicit_task_id_is_used():
    conversation = _conversation_stub()
    store = TaskStore(conversation)

    handle = await store.create(
        title="Task A",
        description="Task A",
        task_id="explicit-id",
    )
    await handle.start()

    assert len(conversation.tasks) == 1
    entry = conversation.tasks[0]
    assert entry["id"] == "explicit-id"


@pytest.mark.asyncio
async def test_add_event_tracks_details():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Run", description="Run")
    await handle.start()

    await handle.add_event("thinking", iteration=1, details={"tokens": 42})
    await handle.add_event("tool_call", iteration=1, details={"tool": "read_file"})

    task = conversation.tasks[0]
    events = task["data"].get("_events", [])
    assert len(events) == 2
    assert events[0]["type"] == "thinking"
    assert events[0]["iteration"] == 1
    assert events[0]["details"]["tokens"] == 42
    assert events[1]["type"] == "tool_call"
    assert events[1]["iteration"] == 1
    assert events[1]["details"]["tool"] == "read_file"


@pytest.mark.asyncio
async def test_add_event_tool_call_with_tools_list():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Run", description="Run")
    await handle.start()

    await handle.add_event(
        "tool_call",
        iteration=1,
        details={"count": 2, "tools": ["read_file", "bash"]},
    )

    task = conversation.tasks[0]
    events = task["data"].get("_events", [])
    assert len(events) == 1
    assert events[0]["details"]["tools"] == ["read_file", "bash"]
    assert events[0]["details"]["count"] == 2


@pytest.mark.asyncio
async def test_add_event_tool_call_with_tool_summaries():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Run", description="Run")
    await handle.start()

    summaries = [
        {"name": "read_file", "arguments": '{"path": "/foo/bar.txt"}'},
        {"name": "bash", "arguments": '{"command": "ls -la"}'},
    ]
    await handle.add_event(
        "tool_call",
        iteration=1,
        details={
            "count": 2,
            "tools": ["read_file", "bash"],
            "tool_summaries": summaries,
        },
    )

    task = conversation.tasks[0]
    events = task["data"].get("_events", [])
    assert len(events) == 1
    assert events[0]["details"]["tool_summaries"] == summaries


@pytest.mark.asyncio
async def test_add_event_tool_result_with_result_details():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Run", description="Run")
    await handle.start()

    results = [
        {"tool_call_id": "tc_1", "is_error": False, "content_preview": "file contents"},
        {
            "tool_call_id": "tc_2",
            "is_error": True,
            "content_preview": "error: not found",
        },
    ]
    await handle.add_event(
        "tool_result",
        iteration=1,
        details={"duration_ms": 150, "count": 2, "results": results},
    )

    task = conversation.tasks[0]
    events = task["data"].get("_events", [])
    assert len(events) == 1
    assert events[0]["details"]["duration_ms"] == 150
    assert events[0]["details"]["count"] == 2
    assert events[0]["details"]["results"] == results


@pytest.mark.asyncio
async def test_add_event_response_with_preview():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(title="Run", description="Run")
    await handle.start()

    await handle.add_event(
        "response",
        iteration=1,
        details={
            "length": 50,
            "loop_state": "TERMINATE",
            "termination_reason": "completed",
            "preview": "Here is the answer to your question...",
        },
    )

    task = conversation.tasks[0]
    events = task["data"].get("_events", [])
    assert len(events) == 1
    assert events[0]["details"]["preview"] == "Here is the answer to your question..."
    assert events[0]["details"]["termination_reason"] == "completed"


@pytest.mark.asyncio
async def test_complete_sets_terminal_metadata():
    conversation = _conversation_stub()
    store = TaskStore(conversation)
    handle = await store.create(
        title="Run",
        description="Run",
        data={"started_at": "2026-04-18T00:00:00+00:00"},
    )
    await handle.start()

    await handle.complete(result="done")
    task = conversation.tasks[0]
    assert task["status"] == "completed"
    assert task["completed_at"] is not None
    assert task["data"]["result"] == "done"


@pytest.mark.asyncio
async def test_track_context_marks_failed_on_exception():
    conversation = _conversation_stub()
    store = TaskStore(conversation)

    with pytest.raises(RuntimeError):
        async with store.track(title="Tracked", description="Tracked") as handle:
            assert handle.id
            raise RuntimeError("boom")

    assert len(conversation.tasks) == 1
    task = conversation.tasks[0]
    assert task["status"] == "failed"
    assert task["data"]["failure_reason"] == "boom"
