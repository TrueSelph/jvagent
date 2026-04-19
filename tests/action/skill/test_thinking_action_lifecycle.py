"""Lifecycle tests for SkillInteractAction task management."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_interact_action import SkillInteractAction


def _mock_action():
    action = MagicMock(spec=SkillInteractAction)
    action.get_class_name = MagicMock(return_value="SkillInteractAction")
    action._ensure_interaction = MagicMock(return_value=True)
    action._discover_skill_bundles = AsyncMock(return_value={})
    action._run_agentic_loop = AsyncMock(return_value=("final", "completed"))
    action.publish = AsyncMock()
    action.unrecord_action_execution = AsyncMock()
    action.task_sync_every_steps = 3
    action.local_tools_path = None
    action.tool_servers = []
    action.call_timeout_seconds = 30.0
    action.skills = []
    action.skills_source = "both"
    action.stream_tool_progress = False
    action.stream_thinking = False
    action.commit_intermediate_messages = True
    action.relay_thoughts_to_channels = False
    return action


def _visitor_with_tasks():
    visitor = MagicMock()
    visitor.utterance = "hello"
    visitor.interaction = SimpleNamespace(
        utterance="hello",
        set_to_executed=MagicMock(),
    )
    visitor.conversation = MagicMock()

    task_handle = MagicMock()
    task_handle.complete = AsyncMock(return_value=True)

    @asynccontextmanager
    async def _track(**_kwargs):
        yield task_handle

    visitor.tasks = MagicMock()
    visitor.tasks.track = _track
    return visitor, task_handle


@pytest.mark.asyncio
async def test_execute_completes_task_and_cleans_up():
    action = _mock_action()
    visitor, task_handle = _visitor_with_tasks()

    tool_executor = MagicMock()
    tool_executor.initialize = AsyncMock()
    tool_executor.get_tool_names = MagicMock(return_value=["search"])
    tool_executor.cleanup = AsyncMock()

    with patch(
        "jvagent.action.skill.skill_interact_action.ToolExecutor",
        return_value=tool_executor,
    ):
        await SkillInteractAction.execute(action, visitor)

    task_handle.complete.assert_awaited_once()
    tool_executor.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_still_cleans_up_on_error():
    action = _mock_action()
    action._run_agentic_loop = AsyncMock(side_effect=RuntimeError("loop exploded"))
    visitor, _ = _visitor_with_tasks()
    visitor.unrecord_action_execution = AsyncMock()

    tool_executor = MagicMock()
    tool_executor.initialize = AsyncMock()
    tool_executor.get_tool_names = MagicMock(return_value=["search"])
    tool_executor.cleanup = AsyncMock()

    with patch(
        "jvagent.action.skill.skill_interact_action.ToolExecutor",
        return_value=tool_executor,
    ):
        await SkillInteractAction.execute(action, visitor)

    visitor.unrecord_action_execution.assert_awaited_once()
    tool_executor.cleanup.assert_awaited_once()
