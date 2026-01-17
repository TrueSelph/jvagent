"""Tests for multi-entity extraction in interview actions."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.foundation.enums import InterviewState, ValidationStatus
from jvagent.memory import Interaction


@pytest.fixture
def sample_question_index():
    """Sample question index for testing."""
    return [
        {
            "name": "user_name",
            "question": "What's your full name?",
            "constraints": {
                "description": "The user's full name",
                "type": "string"
            },
            "required": True
        },
        {
            "name": "available_times",
            "question": "What times are you available?",
            "constraints": {
                "description": "Available training times",
                "type": "string"
            },
            "required": True
        },
        {
            "name": "user_email",
            "question": "What is your email?",
            "constraints": {
                "description": "Email address",
                "type": "string",
                "format": "email"
            },
            "required": True
        }
    ]


@pytest.fixture
async def mock_interview_action(sample_question_index):
    """Create a mock InterviewInteractAction."""
    action = MagicMock()
    action.question_index = sample_question_index
    action.get_class_name = MagicMock(return_value="TestInterviewAction")
    action._get_question_node = AsyncMock()
    action._respond_with_directive = AsyncMock()
    action._sort_by_question_order = MagicMock(side_effect=lambda fields, session: sorted(fields))
    action._update_reachable_questions = AsyncMock()
    return action


@pytest.fixture
async def mock_session(sample_question_index, test_db):
    """Create a test interview session."""
    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_index=sample_question_index,
        state=InterviewState.ACTIVE,
    )
    return session


@pytest.fixture
def mock_question_node():
    """Create a mock QuestionNode."""
    node = MagicMock()
    node.state = {"name": "test_field", "constraints": {}}
    node.label = "test_field"
    return node


class TestMultiEntityExtraction:
    """Test multi-entity extraction scenarios."""

    @pytest.mark.asyncio
    async def test_all_valid_extractions_stored(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that all valid extractions are stored when multiple entities are extracted."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        # Mock extracted responses
        responses = {
            "user_name": "Eldon Marks",
            "available_times": "Mondays at 9am",
            "user_email": "eldon@mail.com"
        }
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        question_walker = QuestionWalker()
        question_walker.should_process_question = AsyncMock(return_value=True)
        question_walker.process_and_validate = AsyncMock(
            side_effect=[
                ("Eldon Marks", ValidationStatus.VALID, None),
                ("Mondays at 9am", ValidationStatus.VALID, None),
                ("eldon@mail.com", ValidationStatus.VALID, None),
            ]
        )
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify all responses were stored
        assert mock_session.get_response("user_name") == "Eldon Marks"
        assert mock_session.get_response("available_times") == "Mondays at 9am"
        assert mock_session.get_response("user_email") == "eldon@mail.com"
        
        # Verify active_question_key is cleared
        assert mock_session.active_question_key is None
        
        # Verify no error directives were sent
        mock_interview_action._respond_with_directive.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_valid_extractions(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that valid extractions are stored even when some are invalid."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        # Mock extracted responses - first valid, second invalid, third valid
        responses = {
            "user_name": "Eldon Marks",
            "available_times": "",  # Invalid (empty)
            "user_email": "eldon@mail.com"
        }
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        question_walker = QuestionWalker()
        question_walker.should_process_question = AsyncMock(return_value=True)
        question_walker.process_and_validate = AsyncMock(
            side_effect=[
                ("Eldon Marks", ValidationStatus.VALID, None),
                ("", ValidationStatus.INVALID, "This field is required."),
                ("eldon@mail.com", ValidationStatus.VALID, None),
            ]
        )
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify valid responses were stored
        assert mock_session.get_response("user_name") == "Eldon Marks"
        assert mock_session.get_response("user_email") == "eldon@mail.com"
        
        # Verify invalid response was NOT stored
        assert mock_session.get_response("available_times") is None
        
        # Verify active_question_key points to first invalid field
        assert mock_session.active_question_key == "available_times"
        
        # Verify error directive was sent for first invalid field
        mock_interview_action._respond_with_directive.assert_called_once()
        call_args = mock_interview_action._respond_with_directive.call_args
        assert "This field is required" in call_args[0][1] or "available_times" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_processing_order_respects_question_index(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that fields are processed in question_index order, not extraction order."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        # Mock extracted responses in different order
        responses = {
            "user_email": "eldon@mail.com",  # Third in question_index
            "user_name": "Eldon Marks",      # First in question_index
            "available_times": "Mondays at 9am"  # Second in question_index
        }
        
        # Track processing order
        processing_order = []
        
        def track_order(field, *args, **kwargs):
            processing_order.append(field)
            return ("value", ValidationStatus.VALID, None)
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        mock_interview_action._sort_by_question_order = MagicMock(
            side_effect=lambda fields, session: sorted(
                fields,
                key=lambda f: next(
                    (i for i, q in enumerate(session.question_index) if q.get("name") == f),
                    len(session.question_index)
                )
            )
        )
        
        question_walker = QuestionWalker()
        question_walker.should_process_question = AsyncMock(return_value=True)
        question_walker.process_and_validate = AsyncMock(side_effect=track_order)
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify processing order matches question_index order
        assert processing_order == ["user_name", "available_times", "user_email"]

    @pytest.mark.asyncio
    async def test_conditional_edge_skips_unreachable_questions(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that questions skipped by conditional edges are not processed."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        # Add conditional question to index
        conditional_index = [
            {
                "name": "training_type",
                "question": "What type of training?",
                "constraints": {"description": "Training type", "type": "string"},
                "required": True
            },
            {
                "name": "advanced_topic",
                "question": "Which advanced topic?",
                "constraints": {"description": "Advanced topic", "type": "string"},
                "required": False,
                "branches": [
                    {
                        "condition": {"question": "training_type", "equals": "advanced"},
                        "target": "advanced_topic"
                    }
                ]
            }
        ]
        mock_session.question_index = conditional_index
        
        # Mock extracted responses - includes advanced_topic but training_type is not "advanced"
        responses = {
            "training_type": "basic",
            "advanced_topic": "machine learning"  # Should be skipped
        }
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        question_walker = QuestionWalker()
        
        # Mock should_process_question to return False for advanced_topic
        async def should_process(question_name, session):
            if question_name == "advanced_topic":
                # Check if condition is met
                return session.get_response("training_type") == "advanced"
            return True
        
        question_walker.should_process_question = AsyncMock(side_effect=should_process)
        question_walker.process_and_validate = AsyncMock(
            return_value=("value", ValidationStatus.VALID, None)
        )
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify training_type was stored
        assert mock_session.get_response("training_type") == "basic"
        
        # Verify advanced_topic was NOT stored (skipped by conditional)
        assert mock_session.get_response("advanced_topic") is None
        
        # Verify process_and_validate was only called for training_type
        assert question_walker.process_and_validate.call_count == 1

    @pytest.mark.asyncio
    async def test_valid_with_flag_continues_processing(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that VALID_WITH_FLAG responses are stored and processing continues."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        responses = {
            "user_name": "Eldon Marks",
            "available_times": "next Tuesday",  # Ambiguous - VALID_WITH_FLAG
            "user_email": "eldon@mail.com"
        }
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        question_walker = QuestionWalker()
        question_walker.should_process_question = AsyncMock(return_value=True)
        question_walker.process_and_validate = AsyncMock(
            side_effect=[
                ("Eldon Marks", ValidationStatus.VALID, None),
                ("next Tuesday", ValidationStatus.VALID_WITH_FLAG, "Got it. Let me clarify the specific time."),
                ("eldon@mail.com", ValidationStatus.VALID, None),
            ]
        )
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify all responses were stored (including VALID_WITH_FLAG)
        assert mock_session.get_response("user_name") == "Eldon Marks"
        assert mock_session.get_response("available_times") == "next Tuesday"
        assert mock_session.get_response("user_email") == "eldon@mail.com"
        
        # Verify clarification directive was sent
        mock_interview_action._respond_with_directive.assert_called()
        # Check that clarification message was sent
        call_args_list = mock_interview_action._respond_with_directive.call_args_list
        clarification_sent = any(
            "clarify" in str(call[0][1]).lower() or "time" in str(call[0][1]).lower()
            for call in call_args_list
        )
        assert clarification_sent

    @pytest.mark.asyncio
    async def test_first_invalid_tracked_correctly(
        self, mock_interview_action, mock_session, mock_question_node
    ):
        """Test that only the first invalid field triggers an error directive."""
        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
        
        responses = {
            "user_name": "Eldon Marks",
            "available_times": "",  # First invalid
            "user_email": "invalid-email"  # Second invalid (should be ignored)
        }
        
        # Setup mocks
        mock_interview_action._get_question_node = AsyncMock(return_value=mock_question_node)
        question_walker = QuestionWalker()
        question_walker.should_process_question = AsyncMock(return_value=True)
        question_walker.process_and_validate = AsyncMock(
            side_effect=[
                ("Eldon Marks", ValidationStatus.VALID, None),
                ("", ValidationStatus.INVALID, "This field is required."),
                ("invalid-email", ValidationStatus.INVALID, "Please provide a valid email address."),
            ]
        )
        
        visitor = MagicMock()
        interaction = MagicMock(spec=Interaction)
        
        # Process responses
        await InterviewInteractAction._process_responses_to_questions(
            mock_interview_action,
            responses,
            mock_session,
            visitor,
            interaction,
            question_walker
        )
        
        # Verify only first valid was stored
        assert mock_session.get_response("user_name") == "Eldon Marks"
        assert mock_session.get_response("available_times") is None
        assert mock_session.get_response("user_email") is None
        
        # Verify active_question_key points to first invalid
        assert mock_session.active_question_key == "available_times"
        
        # Verify only one error directive was sent (for first invalid)
        assert mock_interview_action._respond_with_directive.call_count == 1
        call_args = mock_interview_action._respond_with_directive.call_args
        # Should mention the first invalid field
        assert "required" in str(call_args[0][1]).lower() or "available_times" in str(call_args[0][1]).lower()

