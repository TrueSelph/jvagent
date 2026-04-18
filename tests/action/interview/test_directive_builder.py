"""Tests for interview directive builder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.foundation.prompts import (
    ACTIVE_TASK_DESCRIPTION_TEMPLATE,
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
)
from jvagent.action.interview.core.processing.directive_builder import DirectiveBuilder


def _active_task_description(metadata_title: str, action_description: str) -> str:
    """Mirror DirectiveBuilder title parsing for expected task description strings."""
    action_title = (
        metadata_title.split("Interact")[0].strip().replace("Action", "").strip()
    )
    return ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
        action_title=action_title,
        action_description=action_description,
    )


def _task_result(task_id: str = "t-1"):
    result = MagicMock()
    result.task_id = task_id
    return result


class TestDirectiveBuilderResetTaskTracking:
    """Test reset_task_tracking."""

    def test_reset_task_tracking_clears_task_added(self):
        """reset_task_tracking sets _task_added to False."""
        action = MagicMock()
        builder = DirectiveBuilder(action)
        builder._task_added = True
        builder.reset_task_tracking()
        assert builder._task_added is False


class TestDirectiveBuilderEventOncePerRun:
    """Test that event is added only once per execution."""

    @pytest.mark.asyncio
    async def test_queue_directive_adds_active_task_once(self):
        """First queue_directive adds active task; second does not."""
        action = MagicMock()
        action.get_class_name.return_value = "TestInterview"
        action.metadata = {"title": "TestInterview InteractAction"}
        action.description = ""

        visitor = MagicMock()
        visitor.tasks = MagicMock()
        visitor.tasks.start = AsyncMock(return_value=_task_result())
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state = InterviewState.ACTIVE
        visitor.interview_session.interview_type = "TestInterview"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First directive")
        await builder.queue_directive(visitor, "Second directive")

        visitor.tasks.start.assert_called_once()
        call_kwargs = visitor.tasks.start.call_args[1]
        assert call_kwargs["description"] == _active_task_description(
            "TestInterview InteractAction", ""
        )
        assert call_kwargs["action_name"] == "TestInterview"
        assert call_kwargs["task_type"] == "INTERVIEW"
        assert call_kwargs["singleton_action"] is True
        assert visitor.add_directive.call_count == 2

    @pytest.mark.asyncio
    async def test_reset_allows_active_task_on_next_run(self):
        """After reset_task_tracking, active task is added again."""
        action = MagicMock()
        action.get_class_name.return_value = "TestInterview"
        action.metadata = {"title": "TestInterview InteractAction"}
        action.description = ""

        visitor = MagicMock()
        visitor.tasks = MagicMock()
        visitor.tasks.start = AsyncMock(return_value=_task_result())
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state = InterviewState.ACTIVE
        visitor.interview_session.interview_type = "TestInterview"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First run")
        assert visitor.tasks.start.call_count == 1

        builder.reset_task_tracking()
        await builder.queue_directive(visitor, "Second run")
        assert visitor.tasks.start.call_count == 2


class TestDirectiveBuilderGenerateCancelledDirective:
    """Test generate_cancelled_directive adds cancel event."""

    @pytest.mark.asyncio
    async def test_generate_cancelled_directive_adds_cancel_event(self):
        """When user cancels, add_event is called with cancellation event message."""
        action = MagicMock()
        action.get_class_name.return_value = "SignupInterviewInteractAction"
        action.metadata = {"title": "SignupInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_cancelled_handler.return_value = None  # Use generic path
        action.get_state_event_message.return_value = (
            CANCELLATION_EVENT_MESSAGE_TEMPLATE.format(
                class_name="SignupInterviewInteractAction"
            )
        )
        action.cancellation_message = CANCELLATION_MESSAGE_TEMPLATE

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.update_status = AsyncMock()

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        expected_event = CANCELLATION_EVENT_MESSAGE_TEMPLATE.format(
            class_name="SignupInterviewInteractAction"
        )
        visitor.add_event.assert_called_once_with(expected_event)
        visitor.tasks.update_status.assert_called_once_with(
            status="cancelled",
            description=_active_task_description(
                "SignupInterviewInteractAction InteractAction", ""
            ),
            action_name="SignupInterviewInteractAction",
        )
        visitor.add_directive.assert_called_once_with(CANCELLATION_MESSAGE_TEMPLATE)


class TestDirectiveBuilderGenerateCompletedDirective:
    """Test generate_completed_directive uses update_task."""

    @pytest.mark.asyncio
    async def test_generate_completed_directive_updates_task_to_completed(self):
        """When interview completes, update_task is called with completed."""
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_completion_handler.return_value = None
        action.get_state_event_message.return_value = "Task completed"
        action.completion_message = "Thanks for completing the report"

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.update_status = AsyncMock()

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_completed_directive(session, visitor)

        visitor.tasks.update_status.assert_called_once_with(
            status="completed",
            description=_active_task_description(
                "ReportInterviewInteractAction InteractAction", ""
            ),
            action_name="ReportInterviewInteractAction",
        )
