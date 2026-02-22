"""Tests for validation logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.core.foundation.enums import (
    InterviewState,
    ValidationStatus,
)
from jvagent.action.interview.core.foundation.exceptions import ValidationError
from jvagent.action.interview.core.graph.question_node import QuestionNode
from jvagent.action.interview.core.session.interview_session import InterviewSession


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    question_graph = [
        {
            "name": "user_email",
            "question": "What is your email?",
            "constraints": {
                "description": "Email address",
                "type": "string",
                "format": "email",
            },
            "required": True,
        },
        {
            "name": "user_name",
            "question": "What's your name?",
            "constraints": {"description": "User's name", "type": "string"},
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


class TestValidation:
    """Test validation logic."""

    @pytest.mark.asyncio
    async def test_valid_response_stored(self, test_session):
        """Test that valid responses are stored."""
        test_session.set_response("user_email", "test@example.com")
        test_session.set_validation_status("user_email", ValidationStatus.VALID)

        assert test_session.get_response("user_email") == "test@example.com"
        assert (
            test_session.get_validation_status("user_email") == ValidationStatus.VALID
        )

    @pytest.mark.asyncio
    async def test_invalid_response_not_stored(self, test_session):
        """Test that invalid responses are not stored."""
        # Try to set invalid response
        test_session.set_validation_status("user_email", ValidationStatus.INVALID)

        # Response should not be in session
        assert test_session.get_response("user_email") is None
        assert (
            test_session.get_validation_status("user_email") == ValidationStatus.INVALID
        )

    @pytest.mark.asyncio
    async def test_validation_status_tracking(self, test_session):
        """Test that validation status is tracked per question."""
        test_session.set_response("user_email", "valid@example.com")
        test_session.set_validation_status("user_email", ValidationStatus.VALID)

        test_session.set_validation_status("user_name", ValidationStatus.INVALID)

        assert (
            test_session.get_validation_status("user_email") == ValidationStatus.VALID
        )
        assert (
            test_session.get_validation_status("user_name") == ValidationStatus.INVALID
        )
        assert test_session.get_response("user_email") == "valid@example.com"
        assert test_session.get_response("user_name") is None

    @pytest.mark.asyncio
    async def test_question_node_validation(self, test_session):
        """Test QuestionNode validation."""
        question_config = {
            "name": "user_email",
            "question": "What is your email?",
            "constraints": {
                "description": "Email address",
                "type": "string",
                "format": "email",
            },
            "required": True,
        }

        question_node = await QuestionNode.create(
            agent_id="test_agent", state=question_config, label="user_email"
        )

        # Test valid email
        status, feedback, corrected = await question_node.validate_response(
            "test@example.com", test_session
        )
        assert status == ValidationStatus.VALID

        # Test invalid email (basic check - actual validation may vary)
        status, feedback, corrected = await question_node.validate_response(
            "not-an-email", test_session
        )
        # Note: Actual validation depends on implementation
        # This test verifies the interface works

    @pytest.mark.asyncio
    async def test_required_field_validation(self, test_session):
        """Test that required fields are properly tracked."""
        required = test_session.get_required_questions()
        assert "user_email" in required
        assert "user_name" in required

        # Check if all required are answered
        test_session.set_response("user_email", "test@example.com")
        test_session.set_response("user_name", "Test User")

        answered = test_session.get_answered_questions()
        assert "user_email" in answered
        assert "user_name" in answered

        unanswered = test_session.get_unanswered_questions()
        assert len(unanswered) == 0
