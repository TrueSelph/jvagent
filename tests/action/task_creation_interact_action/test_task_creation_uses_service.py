"""TaskCreationInteractAction should use TaskStore via visitor.tasks."""

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
                "not_before": "2026-04-18T10:30",
                "trigger_on": "schedule",
                "trigger_keyword": "none",
                "trigger_mood": "none",
                "trigger_condition": "none",
                "context": "check-in",
                "priority": "0",
            }
        ]
    )
    action.get_model_action = AsyncMock()
    action.get_class_name = MagicMock(return_value="TaskCreationInteractAction")
    action.model = "gpt-4o-mini"
    action._to_proactive_spec = TaskCreationInteractAction._to_proactive_spec.__get__(
        action, TaskCreationInteractAction
    )
    return action


@pytest.mark.asyncio
async def test_execute_uses_visitor_tasks_create_and_complete():
    action = _make_action()
    model_action = MagicMock()
    model_action.generate = AsyncMock(
        return_value=(
            "COMPLETE_TASK: abc123\n"
            "TASK: Follow up tomorrow\n"
            "NOT_BEFORE: 2026-04-18 10:30\n"
            "TRIGGER_ON: schedule\n"
            "TRIGGER_KEYWORD: none\n"
            "TRIGGER_MOOD: none\n"
            "CONTEXT: check-in\n"
            "PRIORITY: 0"
        )
    )
    action.get_model_action.return_value = model_action

    completed_handle = MagicMock()
    completed_handle.complete = AsyncMock()

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
        tasks=[],
        get_tasks=MagicMock(return_value=[]),
    )
    visitor.tasks = MagicMock()
    visitor.tasks.get = MagicMock(return_value=completed_handle)
    visitor.tasks.enqueue_proactive = AsyncMock()
    visitor.tasks.list_queue = MagicMock(return_value=[])

    await TaskCreationInteractAction.execute(action, visitor)

    visitor.tasks.get.assert_called_once_with("abc123")
    completed_handle.complete.assert_awaited_once()
    visitor.tasks.enqueue_proactive.assert_awaited_once()
    call = visitor.tasks.enqueue_proactive.await_args
    spec = call.args[0]
    assert call.kwargs["title"] == "Follow up tomorrow"
    assert call.kwargs["owner_action"] == "TaskCreationInteractAction"
    assert spec.not_before == "2026-04-18T10:30"
    assert spec.trigger_on == "schedule"
