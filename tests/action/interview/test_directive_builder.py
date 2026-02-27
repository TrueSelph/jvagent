"""Tests for interview directive builder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.foundation.prompts import (
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
)
from jvagent.action.interview.core.processing.directive_builder import DirectiveBuilder


class TestDirectiveBuilderResetEventTracking:
    """Test reset_event_tracking."""

    def test_reset_event_tracking_clears_event_added(self):
        """reset_event_tracking sets _event_added to False."""
        action = MagicMock()
        builder = DirectiveBuilder(action)
        builder._event_added = True
        builder.reset_event_tracking()
        assert builder._event_added is False


class TestDirectiveBuilderEventOncePerRun:
    """Test that event is added only once per execution."""

    @pytest.mark.asyncio
    async def test_queue_directive_adds_event_once(self):
        """First queue_directive adds event; second does not."""
        action = MagicMock()
        action.get_class_name.return_value = "TestInterview"
        action.get_state_event_message.return_value = "Ongoing Activity: TestInterview"

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state.value = "ACTIVE"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First directive")
        await builder.queue_directive(visitor, "Second directive")

        visitor.add_event.assert_called_once()
        assert visitor.add_directive.call_count == 2

    @pytest.mark.asyncio
    async def test_reset_allows_event_on_next_run(self):
        """After reset_event_tracking, event is added again."""
        action = MagicMock()
        action.get_class_name.return_value = "TestInterview"
        action.get_state_event_message.return_value = "Ongoing Activity: TestInterview"

        visitor = MagicMock()
        visitor.add_event = AsyncMock()
        visitor.add_directive = AsyncMock()
        visitor.interview_session = MagicMock()
        visitor.interview_session.state.value = "ACTIVE"

        builder = DirectiveBuilder(action)

        await builder.queue_directive(visitor, "First run")
        assert visitor.add_event.call_count == 1

        builder.reset_event_tracking()
        await builder.queue_directive(visitor, "Second run")
        assert visitor.add_event.call_count == 2


class TestDirectiveBuilderGenerateCancelledDirective:
    """Test generate_cancelled_directive adds cancel event."""

    @pytest.mark.asyncio
    async def test_generate_cancelled_directive_adds_cancel_event(self):
        """When user cancels, add_event is called with cancellation event message."""
        action = MagicMock()
        action.get_class_name.return_value = "SignupInterviewInteractAction"
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

        session = MagicMock()
        session.interview_type = "default"

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
        visitor.add_directive.assert_called_once_with(CANCELLATION_MESSAGE_TEMPLATE)
