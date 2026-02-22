"""Tests for interview directive builder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.foundation.prompts import (
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
)
from jvagent.action.interview.core.processing.directive_builder import DirectiveBuilder


class TestDirectiveBuilderGenerateCancelledDirective:
    """Test generate_cancelled_directive adds cancel event."""

    @pytest.mark.asyncio
    async def test_generate_cancelled_directive_adds_cancel_event(self):
        """When user cancels, add_event is called with cancellation event message."""
        action = MagicMock()
        action.get_class_name.return_value = "SignupInterviewInteractAction"
        action.get_cancelled_handler.return_value = None  # Use generic path

        # Mock config.templates (DirectiveBuilder uses action.config.templates)
        templates = MagicMock()
        templates.get_state_event_message.return_value = (
            CANCELLATION_EVENT_MESSAGE_TEMPLATE.format(
                class_name="SignupInterviewInteractAction"
            )
        )
        templates.cancellation_message = CANCELLATION_MESSAGE_TEMPLATE
        action.config.templates = templates

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
