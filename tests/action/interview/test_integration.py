"""Integration tests for interview action."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.foundation.enums import (
    InterviewState,
    ValidationStatus,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.utils.session_utils import (
    cleanup_session,
    sort_fields_by_question_order,
)


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    question_graph = [
        {
            "name": "user_name",
            "question": "What's your name?",
            "constraints": {"description": "User's name", "type": "string"},
            "required": True,
        },
        {
            "name": "user_email",
            "question": "What's your email?",
            "constraints": {
                "description": "Email",
                "type": "string",
                "format": "email",
            },
            "required": True,
        },
    ]

    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_graph=question_graph,
        state=InterviewState.ACTIVE,
    )
    return session


class TestIntegration:
    """Integration tests for interview flow."""

    @pytest.mark.asyncio
    async def test_complete_interview_flow(self, test_session):
        """Test a complete interview flow from start to completion."""
        # Start in ACTIVE state
        assert test_session.state == InterviewState.ACTIVE

        # Answer first question
        test_session.set_response("user_name", "John Doe")
        test_session.set_validation_status("user_name", ValidationStatus.VALID)
        await test_session.save()

        assert "user_name" in test_session.get_answered_questions()
        assert "user_email" in test_session.get_unanswered_questions()

        # Answer second question
        test_session.set_response("user_email", "john@example.com")
        test_session.set_validation_status("user_email", ValidationStatus.VALID)
        await test_session.save()

        # All required questions answered
        answered = test_session.get_answered_questions()
        assert "user_name" in answered
        assert "user_email" in answered

        # Transition to REVIEW
        test_session.transition_to(InterviewState.REVIEW)
        await test_session.save()
        assert test_session.state == InterviewState.REVIEW

        # Transition to COMPLETED
        test_session.transition_to(InterviewState.COMPLETED)
        await test_session.save()
        assert test_session.state == InterviewState.COMPLETED
        assert test_session.completed_at is not None

    @pytest.mark.asyncio
    async def test_session_cleanup_utility(self, test_session):
        """Test session cleanup utility function."""
        visitor = MagicMock()
        visitor.interview_session = test_session

        # Cleanup should work without errors
        await cleanup_session(test_session, visitor, "TestAction")

        # Visitor reference should be cleared
        assert visitor.interview_session is None

    @pytest.mark.asyncio
    async def test_field_sorting_utility(self, test_session):
        """Test field sorting utility."""
        # Fields in random order
        fields = ["user_email", "user_name"]

        # Should be sorted by question_index order
        sorted_fields = sort_fields_by_question_order(fields, test_session)

        assert sorted_fields == ["user_name", "user_email"]

        # Test with unknown field (should go to end)
        fields = ["user_email", "unknown_field", "user_name"]
        sorted_fields = sort_fields_by_question_order(fields, test_session)

        assert sorted_fields[0] == "user_name"
        assert sorted_fields[1] == "user_email"
        assert "unknown_field" in sorted_fields

    @pytest.mark.asyncio
    async def test_session_reset(self, test_session):
        """Test session reset functionality."""
        # Set some state
        test_session.set_response("user_name", "John Doe")
        test_session.state = InterviewState.REVIEW
        test_session.context = {"some": "data"}
        await test_session.save()

        # Reset
        await test_session.reset()

        # Should be back to initial state
        assert test_session.state == InterviewState.ACTIVE
        assert len(test_session.responses) == 0
        assert len(test_session.context) == 0
        assert test_session.target_node is None
        assert test_session.completed_at is None
        # Interview type and conversation_id should be preserved
        assert test_session.interview_type == "TestInterviewAction"

    @pytest.mark.asyncio
    async def test_update_queue_management(self, test_session):
        """Test update queue helpers."""
        # Set initial response
        test_session.set_response("user_name", "John Doe")
        await test_session.save()

        # Add entry to update queue
        test_session.update_queue.append(
            {
                "field": "user_name",
                "value": "Jane Doe",
                "old_value": "John Doe",
            }
        )

        # Check has_pending_update
        assert test_session.has_pending_update("user_name") is True
        assert test_session.has_pending_update("nonexistent") is False

        # Pop update
        entry = test_session.pop_update("user_name")
        assert entry is not None
        assert entry["field"] == "user_name"
        assert entry["value"] == "Jane Doe"
        assert entry["old_value"] == "John Doe"

        # Queue should be empty now
        assert test_session.has_pending_update("user_name") is False
        assert test_session.pop_update("user_name") is None

    @pytest.mark.asyncio
    async def test_extract_data(self, test_session):
        """Test extracting session data."""
        test_session.set_response("user_name", "John Doe")
        test_session.set_response("user_email", "john@example.com")
        test_session.set_validation_status("user_name", ValidationStatus.VALID)
        test_session.set_validation_status("user_email", ValidationStatus.VALID)
        await test_session.save()

        data = test_session.extract_data()

        assert data["interview_type"] == "TestInterviewAction"
        assert data["responses"]["user_name"] == "John Doe"
        assert data["responses"]["user_email"] == "john@example.com"
        assert "validation_results" in data
        assert "started_at" in data
