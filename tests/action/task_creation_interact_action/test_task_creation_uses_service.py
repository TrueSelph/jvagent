"""TaskCreationInteractAction should use TaskService via visitor.tasks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.task_creation_interact_action.task_creation_interact_action import (
    TaskCreationInteractAction,
)


def _make_action():
    action = MagicMock(spec=TaskCreationInteractAction)
    action._should_skip_scheduling = AsyncMock(return_value=False)
    action._get_capabilities = AsyncMock(return_value="capabilities")
    action._extract_tasks = MagicMock(
        return_value=[
            {
                "description": "Follow up tomorrow",
                "trigger_time": "2026-04-18T10:30",
                "trigger_condition": "none",
                "context": "check-in",
            }
        ]
    )
    action.get_model_action = AsyncMock()
    action.get_class_name = MagicMock(return_value="TaskCreationInteractAction")
    action.model = "gpt-4o-mini"
    return action


@pytest.mark.asyncio
async def test_execute_uses_visitor_tasks_start_and_complete():
    action = _make_action()
    model_action = MagicMock()
    model_action.generate = AsyncMock(
        return_value="COMPLETE_TASK: abc123\nTASK: Follow up tomorrow\nTRIGGER_TIME: 2026-04-18 10:30\nTRIGGER_CONDITION: none\nCONTEXT: check-in"
    )
    action.get_model_action.return_value = model_action

    visitor = MagicMock()
    interaction = SimpleNamespace(
        interpretation="INTENT",
        utterance="hello",
        channel="default",
        get_conversation=AsyncMock(),
    )
    visitor.interaction = interaction
    visitor.conversation = SimpleNamespace(
        get_interaction_history=AsyncMock(return_value=[]),
        session_id="sess-1",
        active_tasks=[],
        get_active_tasks=MagicMock(return_value=[]),
    )
    visitor.tasks = MagicMock()
    visitor.tasks.complete = AsyncMock(return_value=True)
    visitor.tasks.start = AsyncMock()

    await TaskCreationInteractAction.execute(action, visitor)

    visitor.tasks.complete.assert_awaited_once_with(task_id="abc123")
    visitor.tasks.start.assert_awaited_once()
    kwargs = visitor.tasks.start.await_args.kwargs
    assert kwargs["task_type"] == "PROACTIVE"
    assert kwargs["trigger_at"] == "2026-04-18T10:30"
