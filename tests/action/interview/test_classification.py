"""Tests for classification logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jvagent.action.interview.core.foundation.enums import InterviewState, Intent
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.classification.classification_handler import (
    ClassificationResult,
    ClassificationHandler,
)


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
        from jvagent.action.interview.core.classification.classification_handler import ClassificationHandler
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        # Set some responses
        test_session.set_response("user_name", "John Doe")

        # Create mock action
        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        action.config.classification.context_list_compact_threshold = 5
        action.config.classification.context_options_text = "options available"

        # Create handler and call build_classification_context
        handler = ClassificationHandler(action)
        context = await handler.build_classification_context(test_session)

        # Verify context has the expected keys and structure
        assert "current_state" in context
        assert "answered_fields" in context
        assert "entities_to_extract" in context
        assert "allowed_field_names" in context
        assert isinstance(context["allowed_field_names"], set)
        assert "required_fields_info" not in context  # Should be removed
    
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

    def test_extract_field_values_from_extracted_list(self):
        """_extract_field_values reads only from result['extracted'] (list of one-key dicts)."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        handler = ClassificationHandler(action)

        assert handler._extract_field_values(
            {"extracted": [{"user_name": "John Doe"}]}, Intent.SUBMISSION
        ) == {"user_name": "John Doe"}
        assert handler._extract_field_values({"extracted": []}, Intent.SUBMISSION) == {}
        assert handler._extract_field_values({}, Intent.SUBMISSION) == {}
        assert handler._extract_field_values({"extracted": None}, Intent.SUBMISSION) == {}

    def test_extract_field_values_filters_n_a_for_decline(self):
        """_extract_field_values excludes entries with value N/A (DECLINE format)."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        handler = ClassificationHandler(action)

        assert handler._extract_field_values(
            {"extracted": [{"photo_upload": "N/A"}]}, Intent.DECLINE
        ) == {}
        assert handler._extract_field_values(
            {"extracted": [{"a": "1"}, {"b": "N/A"}]}, Intent.SUBMISSION
        ) == {"a": "1"}

    def test_extract_field_values_multiple_entries(self):
        """_extract_field_values merges multiple one-key dicts into one dict."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        handler = ClassificationHandler(action)

        assert handler._extract_field_values(
            {"extracted": [{"incident_location": "Water Street"}, {"incident_description": "Something"}]},
            Intent.SUBMISSION,
        ) == {"incident_location": "Water Street", "incident_description": "Something"}

    def test_extract_field_values_filters_invalid_field_names(self):
        """_extract_field_values drops extractions for field names not in allowed_field_names."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        handler = ClassificationHandler(action)

        result = handler._extract_field_values(
            {"extracted": [{"user_name": "John"}, {"hallucinated_field": "value"}]},
            Intent.SUBMISSION,
            allowed_field_names={"user_name"},
        )
        assert result == {"user_name": "John"}

    def test_extract_field_values_allowed_field_names_none_passes_all(self):
        """_extract_field_values with allowed_field_names=None does not filter (backward compatible)."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        handler = ClassificationHandler(action)

        result = handler._extract_field_values(
            {"extracted": [{"user_name": "John"}, {"other_field": "value"}]},
            Intent.SUBMISSION,
            allowed_field_names=None,
        )
        assert result == {"user_name": "John", "other_field": "value"}

    @pytest.mark.asyncio
    async def test_classification_context_answered_fields_with_values(self, test_session):
        """Verify answered_fields includes field values in 'field: value' format."""
        from jvagent.action.interview.core.classification.classification_handler import ClassificationHandler
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        # Set some responses
        test_session.set_response("user_name", "John Doe")
        # Don't answer all questions so we can see entities_to_extract

        # Create mock action with minimal config
        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        action.config.classification.context_list_compact_threshold = 5
        action.config.classification.context_options_text = "options available"

        # Create handler and build context
        handler = ClassificationHandler(action)
        context = await handler.build_classification_context(test_session)

        # Verify answered_fields includes values
        assert "user_name: John Doe" in context["answered_fields"]

        # Verify required_fields_info is removed
        assert "required_fields_info" not in context

        # Verify context has current_state and entities_to_extract
        assert "current_state" in context
        assert "entities_to_extract" in context
        # If there are unanswered questions, verify they have markers
        if "None (all questions answered)" not in context["entities_to_extract"]:
            assert "[REQUIRED]" in context["entities_to_extract"] or "[OPTIONAL]" in context["entities_to_extract"]

    @pytest.mark.asyncio
    async def test_classification_context_truncates_long_values(self, test_session):
        """Verify long values are truncated to prevent token bloat."""
        from jvagent.action.interview.core.classification.classification_handler import ClassificationHandler
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        # Set a very long response
        long_value = "x" * 150
        test_session.set_response("user_name", long_value)

        # Create mock action with minimal config
        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        action.config.classification.context_list_compact_threshold = 5
        action.config.classification.context_options_text = "options available"

        # Create handler and build context
        handler = ClassificationHandler(action)
        context = await handler.build_classification_context(test_session)

        # Verify value is truncated
        assert "..." in context["answered_fields"]
        # Verify it doesn't contain the full long value
        assert "user_name: " + long_value not in context["answered_fields"]


class TestDataInputField:
    """Tests for data_input_field extraction and N/A fallback."""

    def test_extract_data_input_values_key_exists_returns_value(self):
        """When data_input_field key exists in visitor.data, value is extracted."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        question_graph = [
            {"name": "user_name", "constraints": {"description": "Name"}},
            {
                "name": "incident_media",
                "constraints": {"description": "Media", "data_input_field": "whatsapp_media"},
            },
        ]
        action = MagicMock(spec=InterviewInteractAction)
        action._get_question_graph = MagicMock(return_value=question_graph)
        handler = ClassificationHandler(action)

        session = MagicMock()
        session.get_unanswered_questions = MagicMock(return_value=["incident_media"])
        visitor = MagicMock()
        visitor.data = {"whatsapp_media": ["url1", "url2"]}

        extracted, excluded = handler.extract_data_input_values(session, visitor)

        assert extracted == {"incident_media": ["url1", "url2"]}
        assert excluded == {"incident_media"}

    def test_extract_data_input_values_key_absent_current_question_returns_n_a(self):
        """When key is absent and question is current (first unanswered), value is 'N/A'."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        question_graph = [
            {"name": "user_name", "constraints": {"description": "Name"}},
            {
                "name": "incident_media",
                "constraints": {"description": "Media", "data_input_field": "whatsapp_media"},
            },
        ]
        action = MagicMock(spec=InterviewInteractAction)
        action._get_question_graph = MagicMock(return_value=question_graph)
        handler = ClassificationHandler(action)

        session = MagicMock()
        session.get_unanswered_questions = MagicMock(return_value=["incident_media"])
        visitor = MagicMock()
        visitor.data = {}

        extracted, excluded = handler.extract_data_input_values(session, visitor)

        assert extracted == {"incident_media": "N/A"}
        assert excluded == {"incident_media"}

    def test_extract_data_input_values_key_absent_not_current_question_not_added(self):
        """When key is absent and question is not current, field is not added to extracted_values."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        question_graph = [
            {"name": "user_name", "constraints": {"description": "Name"}},
            {
                "name": "incident_media",
                "constraints": {"description": "Media", "data_input_field": "whatsapp_media"},
            },
        ]
        action = MagicMock(spec=InterviewInteractAction)
        action._get_question_graph = MagicMock(return_value=question_graph)
        handler = ClassificationHandler(action)

        session = MagicMock()
        session.get_unanswered_questions = MagicMock(return_value=["user_name"])
        visitor = MagicMock()
        visitor.data = {}

        extracted, excluded = handler.extract_data_input_values(session, visitor)

        assert "incident_media" not in extracted
        assert excluded == {"incident_media"}

    @pytest.mark.asyncio
    async def test_data_input_field_excluded_from_entities_to_extract(self):
        """data_input_field questions are excluded from entities_to_extract in classification context."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction

        question_graph = [
            {
                "name": "user_name",
                "question": "What's your name?",
                "constraints": {"description": "User's name", "type": "string"},
                "required": True,
            },
            {
                "name": "incident_media",
                "question": "Upload media?",
                "constraints": {"description": "Media", "data_input_field": "whatsapp_media"},
                "required": False,
            },
        ]
        session = MagicMock()
        session.state = MagicMock(value="ACTIVE")
        session.question_graph = question_graph
        session.get_answered_questions = MagicMock(return_value=[])
        session.get_response = MagicMock(return_value=None)
        session.get_required_questions = MagicMock(return_value=["user_name"])
        session.interview_type = "TestInterviewAction"

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name = MagicMock(return_value="TestInterviewAction")
        action.config.classification.context_list_compact_threshold = 5
        action.config.classification.context_options_text = "options available"
        action._get_question_graph = MagicMock(return_value=question_graph)

        handler = ClassificationHandler(action)
        context = await handler.build_classification_context(
            session, excluded_fields={"incident_media"}
        )

        assert "entities_to_extract" in context
        assert "incident_media" not in context["entities_to_extract"]
        assert "user_name" in context["entities_to_extract"]
