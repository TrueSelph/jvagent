"""Tests for TaskService with typed TaskRecord integration (workstream 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.services.task_service import TaskService
from jvagent.memory.task_record import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    InvalidTaskTransition,
    TaskRecord,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conversation():
    conv = MagicMock()
    conv.active_tasks = []
    conv.save = AsyncMock()
    return conv


def _make_service(conversation=None):
    conv = conversation or _make_conversation()
    return TaskService(conv), conv


# ---------------------------------------------------------------------------
# TaskService.start uses TaskRecord.create
# ---------------------------------------------------------------------------


class TestTaskServiceStart:
    @pytest.mark.asyncio
    async def test_start_creates_active_task(self):
        svc, conv = _make_service()
        handle = await svc.start(description="test task", task_type="AGENTIC_LOOP")
        assert handle.task_id
        assert len(conv.active_tasks) == 1
        task = conv.active_tasks[0]
        assert task["status"] == "active"
        assert task["description"] == "test task"
        assert task["task_type"] == "AGENTIC_LOOP"

    @pytest.mark.asyncio
    async def test_start_with_action_name_in_id(self):
        svc, conv = _make_service()
        handle = await svc.start(
            description="task", task_type="T", action_name="SkillAction"
        )
        assert "SkillAction" in handle.task_id

    @pytest.mark.asyncio
    async def test_start_saves_conversation(self):
        svc, conv = _make_service()
        await svc.start(description="t", task_type="T")
        conv.save.assert_called()

    @pytest.mark.asyncio
    async def test_start_with_metadata(self):
        svc, conv = _make_service()
        meta = {"iterations": 0, "tools_called": []}
        handle = await svc.start(description="t", task_type="T", metadata=meta)
        task = conv.active_tasks[0]
        assert task["metadata"]["iterations"] == 0


# ---------------------------------------------------------------------------
# TaskService.complete with lifecycle validation
# ---------------------------------------------------------------------------


class TestTaskServiceComplete:
    @pytest.mark.asyncio
    async def test_complete_transitions_to_terminal(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        result = await svc.complete(handle.task_id, status="completed")
        assert result is True
        assert conv.active_tasks[0]["status"] == "completed"
        assert conv.active_tasks[0]["terminal_at"] is not None

    @pytest.mark.asyncio
    async def test_complete_idempotent_when_already_terminal(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        await svc.complete(handle.task_id, status="completed")
        # Completing again should be a no-op, not raise
        result = await svc.complete(handle.task_id, status="failed")
        assert result is True  # idempotent
        # Status should still be "completed" (first terminal wins)
        assert conv.active_tasks[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_invalid_status_raises(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        with pytest.raises(ValueError):
            await svc.complete(handle.task_id, status="bogus_status")

    @pytest.mark.asyncio
    async def test_complete_sets_summary(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        await svc.complete(handle.task_id, status="completed", summary="Done.")
        task = conv.active_tasks[0]
        assert task["metadata"].get("final_summary") == "Done."

    @pytest.mark.asyncio
    async def test_complete_all_terminal_statuses(self):
        for status in TERMINAL_STATUSES:
            svc, conv = _make_service()
            handle = await svc.start(description="t", task_type="T")
            result = await svc.complete(handle.task_id, status=status)
            assert result is True


# ---------------------------------------------------------------------------
# TaskService.get_record (typed access)
# ---------------------------------------------------------------------------


class TestTaskServiceGetRecord:
    @pytest.mark.asyncio
    async def test_get_record_returns_task_record(self):
        svc, conv = _make_service()
        handle = await svc.start(description="typed task", task_type="AGENTIC_LOOP")
        record = svc.get_record(task_id=handle.task_id)
        assert isinstance(record, TaskRecord)
        assert record.description == "typed task"
        assert record.status == "active"

    @pytest.mark.asyncio
    async def test_get_record_returns_none_when_not_found(self):
        svc, _ = _make_service()
        assert svc.get_record(task_id="nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_records_returns_typed_list(self):
        svc, conv = _make_service()
        await svc.start(description="t1", task_type="T", action_name="A")
        await svc.start(description="t2", task_type="T", action_name="A")
        records = svc.list_records(action_name="A")
        assert len(records) == 2
        assert all(isinstance(r, TaskRecord) for r in records)


# ---------------------------------------------------------------------------
# TaskService.fail
# ---------------------------------------------------------------------------


class TestTaskServiceFail:
    @pytest.mark.asyncio
    async def test_fail_sets_failure_reason(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        await svc.fail(handle.task_id, error="tool exploded")
        task = conv.active_tasks[0]
        assert task["status"] == "failed"
        assert "tool exploded" in task["metadata"].get("failure_reason", "")

    @pytest.mark.asyncio
    async def test_fail_not_found_returns_false(self):
        svc, _ = _make_service()
        result = await svc.fail("nonexistent", error="err")
        assert result is False


# ---------------------------------------------------------------------------
# TaskService.sweep_stale
# ---------------------------------------------------------------------------


class TestTaskServiceSweepStale:
    @pytest.mark.asyncio
    async def test_sweep_stale_marks_old_tasks_failed(self):
        svc, conv = _make_service()
        handle = await svc.start(description="stale", task_type="T")
        # Manually backdate heartbeat
        conv.active_tasks[0]["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
        count = await svc.sweep_stale(ttl_seconds=60)
        assert count == 1
        assert conv.active_tasks[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_sweep_stale_ignores_terminal_tasks(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T")
        await svc.complete(handle.task_id)
        # Backdate — terminal tasks should not be swept
        conv.active_tasks[0]["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
        count = await svc.sweep_stale(ttl_seconds=60)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sweep_stale_no_stale(self):
        svc, conv = _make_service()
        await svc.start(description="fresh", task_type="T")
        count = await svc.sweep_stale(ttl_seconds=3600)
        assert count == 0


# ---------------------------------------------------------------------------
# record_step with StepRecord
# ---------------------------------------------------------------------------


class TestTaskServiceRecordStep:
    @pytest.mark.asyncio
    async def test_record_step_appends_to_metadata(self):
        svc, conv = _make_service()
        handle = await svc.start(description="t", task_type="T", metadata={"steps": []})
        await svc.record_step(
            handle.task_id,
            step_type="thinking",
            iteration=1,
            details={"tokens": 250},
        )
        task = conv.active_tasks[0]
        steps = task["metadata"].get("steps", [])
        assert len(steps) == 1
        assert steps[0]["type"] == "thinking"
        assert steps[0]["iteration"] == 1

    @pytest.mark.asyncio
    async def test_record_step_accumulates_tool_calls(self):
        svc, conv = _make_service()
        handle = await svc.start(
            description="t", task_type="T", metadata={"tools_called": []}
        )
        await svc.record_step(
            handle.task_id,
            step_type="tool_call",
            iteration=1,
            details={"tools": ["search", "read"]},
        )
        task = conv.active_tasks[0]
        tools_called = task["metadata"].get("tools_called", [])
        assert "search" in tools_called
        assert "read" in tools_called

    @pytest.mark.asyncio
    async def test_record_step_thinking_accumulates_tokens(self):
        svc, conv = _make_service()
        handle = await svc.start(
            description="t", task_type="T", metadata={"thinking_tokens_used": 0}
        )
        await svc.record_step(
            handle.task_id, step_type="thinking", iteration=1, details={"tokens": 100}
        )
        await svc.record_step(
            handle.task_id, step_type="thinking", iteration=2, details={"tokens": 200}
        )
        task = conv.active_tasks[0]
        assert task["metadata"]["thinking_tokens_used"] == 300
