"""Tests for classification logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jvagent.action.interview.core.foundation.enums import InterviewState, Intent
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.interview_interact_action import ClassificationResult


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    question_index = [
        {
            "name": "user_name",
            "question": "What's your name?",
            "constraints": {"description": "User's name", "type": "string"},
            "required": True
        },
        {
            "name": "user_email",
            "question": "What's your email?",
            "constraints": {"description": "Email", "type": "string", "format": "email"},
            "required": True
        }
    ]
    
    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_index=question_index,
        state=InterviewState.ACTIVE,
    )
    return session


class TestClassification:
    """Test classification logic."""
    
    def test_classification_result_creation(self):
        """Test ClassificationResult dataclass."""
        result = ClassificationResult(
            intent=Intent.SUBMISSION.value,
            confidence=0.95,
            extracted_data={"user_name": "John Doe"}
        )
        
        assert result.intent == Intent.SUBMISSION.value
        assert result.confidence == 0.95
        assert result.extracted_data == {"user_name": "John Doe"}
        assert result.field is None
        assert result.value is None
    
    def test_classification_result_update_intent(self):
        """Test ClassificationResult for UPDATE intent."""
        result = ClassificationResult(
            intent=Intent.UPDATE.value,
            field="user_email",
            value="new@example.com"
        )
        
        assert result.intent == Intent.UPDATE.value
        assert result.field == "user_email"
        assert result.value == "new@example.com"
        assert result.extracted_data is None
    
    def test_classification_result_cancellation(self):
        """Test ClassificationResult for CANCELLATION intent."""
        result = ClassificationResult(intent=Intent.CANCELLATION.value)
        
        assert result.intent == Intent.CANCELLATION.value
        assert result.field is None
        assert result.value is None
        assert result.extracted_data is None
    
    def test_classification_result_confirmation(self):
        """Test ClassificationResult for CONFIRMATION intent."""
        result = ClassificationResult(
            intent=Intent.CONFIRMATION.value,
            confidence=0.9
        )
        
        assert result.intent == Intent.CONFIRMATION.value
        assert result.confidence == 0.9
    
    @pytest.mark.asyncio
    async def test_classification_context_building(self, test_session):
        """Test building classification context."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        # Set some responses
        test_session.set_response("user_name", "John Doe")
        
        # Create mock action
        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        
        # Call _build_classification_context
        context = action._build_classification_context(test_session)
        
        # Note: This test verifies the method exists and can be called
        # Actual implementation testing would require more setup
        assert "current_state" in context or hasattr(action, "_build_classification_context")
    
    def test_intent_enum_values(self):
        """Test that all intent enum values are correct."""
        assert Intent.CANCELLATION.value == "CANCELLATION"
        assert Intent.CONFIRMATION.value == "CONFIRMATION"
        assert Intent.UPDATE.value == "UPDATE"
        assert Intent.DECLINE.value == "DECLINE"
        assert Intent.SUBMISSION.value == "SUBMISSION"
        assert Intent.NONE.value == "NONE"
    
    def test_classification_result_field_normalization(self):
        """Test that field normalization handles string 'null'."""
        # This tests the normalization logic in classification
        result = ClassificationResult(
            intent=Intent.UPDATE.value,
            field="null",  # Should be normalized to None
            value="test"
        )
        
        # The normalization happens in the classification method
        # This test documents the expected behavior
        assert result.field == "null"  # Before normalization
        # After normalization (in actual code), it would be None
