"""Tests for TaskTracker: task creation, step tracking, and completion."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.thinking.task_tracker import TaskTracker


class TestTaskTrackerCreation:
    """Test task creation and lifecycle."""

    @pytest.mark.asyncio
    async def test_create_task(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.get_active_task = MagicMock(return_value=None)

        tracker = TaskTracker(conversation, action_name="TestAction")
        task_id = await tracker.create_task(description="Test task")

        assert task_id.startswith("agentic:")
        assert tracker._created is True
        conversation.add_active_task.assert_called_once()
        call_kwargs = conversation.add_active_task.call_args
        assert call_kwargs.kwargs["description"] == "Test task"
        assert call_kwargs.kwargs["task_type"] == "AGENTIC_LOOP"

    @pytest.mark.asyncio
    async def test_create_task_with_custom_type(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.get_active_task = MagicMock(return_value=None)

        tracker = TaskTracker(conversation)
        task_id = await tracker.create_task(
            description="Custom",
            task_type="CUSTOM_TYPE",
        )
        call_kwargs = conversation.add_active_task.call_args
        assert call_kwargs.kwargs["task_type"] == "CUSTOM_TYPE"


class TestTaskTrackerSteps:
    """Test step recording."""

    @pytest.mark.asyncio
    async def test_add_thinking_step(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.get_active_task = MagicMock(
            return_value={
                "task_id": "agentic:test123",
                "description": "Test",
                "metadata": {},
                "task_type": "AGENTIC_LOOP",
            }
        )

        tracker = TaskTracker(conversation)
        await tracker.create_task(description="Test")
        await tracker.add_step("thinking", iteration=1, details={"tokens": 500})

        assert len(tracker._steps) == 1
        assert tracker._steps[0]["type"] == "thinking"
        assert tracker._thinking_tokens_used == 500

    @pytest.mark.asyncio
    async def test_add_tool_call_step(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.get_active_task = MagicMock(
            return_value={
                "task_id": "agentic:test123",
                "description": "Test",
                "metadata": {},
                "task_type": "AGENTIC_LOOP",
            }
        )

        tracker = TaskTracker(conversation)
        await tracker.create_task(description="Test")
        await tracker.add_step("tool_call", iteration=1, details={"tool": "read_file"})

        assert "read_file" in tracker._tools_called
        assert tracker._iteration_count == 1

    @pytest.mark.asyncio
    async def test_add_step_without_create_is_noop(self):
        conversation = MagicMock()
        tracker = TaskTracker(conversation)
        # Should not raise
        await tracker.add_step("thinking", iteration=1)


class TestTaskTrackerCompletion:
    """Test task completion and failure."""

    @pytest.mark.asyncio
    async def test_complete_task(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.update_task = AsyncMock(return_value=True)
        conversation.get_active_task = MagicMock(
            return_value={
                "task_id": "agentic:test123",
                "description": "Test task",
                "metadata": {},
                "task_type": "AGENTIC_LOOP",
            }
        )

        tracker = TaskTracker(conversation)
        await tracker.create_task(description="Test task")
        await tracker.add_step("thinking", iteration=1)
        await tracker.complete_task(final_status="completed", summary="Done")

        conversation.update_task.assert_called_once()
        call_kwargs = conversation.update_task.call_args
        assert call_kwargs.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_fail_task(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.update_task = AsyncMock(return_value=True)
        conversation.get_active_task = MagicMock(
            return_value={
                "task_id": "agentic:test123",
                "description": "Test",
                "metadata": {},
                "task_type": "AGENTIC_LOOP",
            }
        )

        tracker = TaskTracker(conversation)
        await tracker.create_task(description="Test")
        await tracker.fail_task("Something went wrong")

        conversation.update_task.assert_called_once()
        call_kwargs = conversation.update_task.call_args
        assert call_kwargs.kwargs["status"] == "failed"


class TestTaskTrackerProgress:
    """Test progress summary."""

    @pytest.mark.asyncio
    async def test_get_progress_summary(self):
        conversation = MagicMock()
        conversation.add_active_task = AsyncMock()
        conversation.get_active_task = MagicMock(
            return_value={
                "task_id": "agentic:test123",
                "description": "Test",
                "metadata": {},
                "task_type": "AGENTIC_LOOP",
            }
        )

        tracker = TaskTracker(conversation)
        await tracker.create_task(description="Test")
        await tracker.add_step("thinking", iteration=1)
        await tracker.add_step("tool_call", iteration=1, details={"tool": "search"})

        summary = tracker.get_progress_summary()
        assert summary["iteration"] == 1
        assert summary["steps_completed"] == 2
        assert "search" in summary["tools_called"]
        assert summary["last_action"] == "tool_call"
