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


def _make_mock_handle(task_id: str = "task_123"):
    handle = MagicMock()
    handle.id = task_id
    handle.start = AsyncMock()
    handle.complete = AsyncMock()
    handle.cancel = AsyncMock()
    handle.fail = AsyncMock()
    return handle


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

        mock_handle = _make_mock_handle("t-1")

        visitor = MagicMock()
        visitor.tasks = MagicMock()
        visitor.tasks.create = AsyncMock(return_value=mock_handle)
        # No prior active task → list returns empty so create() runs.
        visitor.tasks.list = MagicMock(return_value=[])
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state = InterviewState.ACTIVE
        visitor.interview_session.interview_type = "TestInterview"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First directive")
        await builder.queue_directive(visitor, "Second directive")

        visitor.tasks.create.assert_called_once()
        call_kwargs = visitor.tasks.create.call_args[1]
        assert call_kwargs["description"] == _active_task_description(
            "TestInterview InteractAction", ""
        )
        assert call_kwargs["owner_action"] == "TestInterview"
        assert call_kwargs["task_type"] == "INTERVIEW"
        assert visitor.add_directive.call_count == 2

    @pytest.mark.asyncio
    async def test_reset_allows_active_task_on_next_run_only_when_none_exists(self):
        """After reset, a NEW active task is created only if none already exists.

        Dedup: when an active task with the same owner_action is still on
        the conversation, ``_start_active_task`` adopts it instead of
        creating a duplicate. Without this guard, every new interaction
        would append another task to the conversation's task list.
        """
        action = MagicMock()
        action.get_class_name.return_value = "TestInterview"
        action.metadata = {"title": "TestInterview InteractAction"}
        action.description = ""

        mock_handle = _make_mock_handle("t-1")
        existing_handle = _make_mock_handle("t-1")
        existing_handle.owner_action = "TestInterview"
        existing_handle.update = AsyncMock()

        visitor = MagicMock()
        visitor.tasks = MagicMock()
        visitor.tasks.create = AsyncMock(return_value=mock_handle)
        # Run 1: no existing active task → create fires.
        # Run 2: existing active task present → create skipped, adopt instead.
        visitor.tasks.list = MagicMock(side_effect=[[], [existing_handle]])
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state = InterviewState.ACTIVE
        visitor.interview_session.interview_type = "TestInterview"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First run")
        assert visitor.tasks.create.call_count == 1

        builder.reset_task_tracking()
        await builder.queue_directive(visitor, "Second run")
        # Still 1 — second run adopted the existing task instead of creating.
        assert visitor.tasks.create.call_count == 1
        # The adopted handle's data was refreshed with the new state metadata.
        existing_handle.update.assert_awaited()
        # _task_id now points at the adopted task.
        assert builder._task_id == "t-1"


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

        mock_handle = _make_mock_handle("t-1")

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=mock_handle)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-1"  # Simulate prior task registration

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        expected_event = CANCELLATION_EVENT_MESSAGE_TEMPLATE.format(
            class_name="SignupInterviewInteractAction"
        )
        visitor.add_event.assert_called_once_with(expected_event)
        mock_handle.cancel.assert_called_once()
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

        mock_handle = _make_mock_handle("t-1")

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=mock_handle)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-1"  # Simulate prior task registration

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_completed_directive(session, visitor)

        mock_handle.complete.assert_called_once()


class TestDirectiveBuilderTaskRemoval:
    """Test that interview tasks are transitioned + removed on terminal events."""

    @pytest.mark.asyncio
    async def test_cancel_transitions_and_deletes_task(self):
        """``generate_cancelled_directive`` transitions task to cancelled then deletes it."""
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_cancelled_handler.return_value = None
        action.get_state_event_message.return_value = "Task cancelled"
        action.cancellation_message = "OK, cancelled."

        mock_handle = _make_mock_handle("t-1")

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=mock_handle)
        visitor.tasks.list = MagicMock(return_value=[])  # no stragglers
        visitor.tasks.delete = AsyncMock(return_value=True)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-1"

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        mock_handle.cancel.assert_called_once()
        visitor.tasks.delete.assert_awaited_once_with("t-1")
        # _task_id cleared so subsequent runs don't try to act on the dead id.
        assert builder._task_id is None

    @pytest.mark.asyncio
    async def test_complete_transitions_and_deletes_task(self):
        """Completion path also deletes the task after transition."""
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_completion_handler.return_value = None
        action.get_state_event_message.return_value = "Task completed"
        action.completion_message = "Done."

        mock_handle = _make_mock_handle("t-1")

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=mock_handle)
        visitor.tasks.list = MagicMock(return_value=[])
        visitor.tasks.delete = AsyncMock(return_value=True)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-1"

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_completed_directive(session, visitor)

        mock_handle.complete.assert_called_once()
        visitor.tasks.delete.assert_awaited_once_with("t-1")
        assert builder._task_id is None

    @pytest.mark.asyncio
    async def test_cancel_sweeps_straggler_active_tasks_for_same_owner(self):
        """Stale active tasks under the same owner_action all get transitioned + deleted."""
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_cancelled_handler.return_value = None
        action.get_state_event_message.return_value = "Task cancelled"
        action.cancellation_message = "OK, cancelled."

        primary = _make_mock_handle("t-primary")
        primary.id = "t-primary"
        straggler_1 = _make_mock_handle("t-stale-1")
        straggler_1.id = "t-stale-1"
        straggler_2 = _make_mock_handle("t-stale-2")
        straggler_2.id = "t-stale-2"

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=primary)
        # Sweep returns the stragglers (primary is excluded since dedup happens
        # in the helper).
        visitor.tasks.list = MagicMock(return_value=[straggler_1, straggler_2])
        visitor.tasks.delete = AsyncMock(return_value=True)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-primary"

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        # Every handle transitioned + deleted.
        primary.cancel.assert_called_once()
        straggler_1.cancel.assert_called_once()
        straggler_2.cancel.assert_called_once()
        delete_ids = {call.args[0] for call in visitor.tasks.delete.await_args_list}
        assert delete_ids == {"t-primary", "t-stale-1", "t-stale-2"}

    @pytest.mark.asyncio
    async def test_failed_transition_preserves_evidence_does_not_delete(self):
        """If ``handle.cancel()`` raises, the task is NOT deleted — preserve trail."""
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_cancelled_handler.return_value = None
        action.get_state_event_message.return_value = "Task cancelled"
        action.cancellation_message = "OK, cancelled."

        mock_handle = _make_mock_handle("t-1")
        # Simulate a transition failure.
        mock_handle.cancel = AsyncMock(side_effect=RuntimeError("DB error"))

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=mock_handle)
        visitor.tasks.list = MagicMock(return_value=[])
        visitor.tasks.delete = AsyncMock(return_value=True)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = "t-1"

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        # Transition was attempted, then delete was NOT called.
        mock_handle.cancel.assert_awaited_once()
        visitor.tasks.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_task_id_but_active_tasks_exist_still_swept(self):
        """``_task_id`` unset but active tasks for the owner_action exist → still cleaned up.

        Edge case: cancellation arrives before any directive was queued (so
        ``_task_id`` is None), but a task was created in a previous interaction
        and remained active. The sweep must still transition + delete it.
        """
        action = MagicMock()
        action.get_class_name.return_value = "ReportInterviewInteractAction"
        action.metadata = {"title": "ReportInterviewInteractAction InteractAction"}
        action.description = ""
        action.get_cancelled_handler.return_value = None
        action.get_state_event_message.return_value = "Task cancelled"
        action.cancellation_message = "OK, cancelled."

        leftover = _make_mock_handle("t-leftover")
        leftover.id = "t-leftover"

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.tasks = MagicMock()
        visitor.tasks.get = MagicMock(return_value=None)
        visitor.tasks.list = MagicMock(return_value=[leftover])
        visitor.tasks.delete = AsyncMock(return_value=True)

        session = MagicMock()
        session.interview_type = "default"
        session.delete = AsyncMock()

        builder = DirectiveBuilder(action)
        builder._task_id = None  # No tracked task

        with patch(
            "jvagent.action.interview.core.utils.session_utils.cleanup_session",
            new_callable=AsyncMock,
        ):
            await builder.generate_cancelled_directive(session, visitor)

        leftover.cancel.assert_called_once()
        visitor.tasks.delete.assert_awaited_once_with("t-leftover")
