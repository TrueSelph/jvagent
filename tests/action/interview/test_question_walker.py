"""Tests for QuestionWalker traversal logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.graph.question_node import QuestionNode


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    question_index = [
        {
            "name": "q1",
            "question": "Question 1?",
            "constraints": {"description": "First question", "type": "string"},
            "required": True
        },
        {
            "name": "q2",
            "question": "Question 2?",
            "constraints": {"description": "Second question", "type": "string"},
            "required": True
        },
        {
            "name": "q3",
            "question": "Question 3?",
            "constraints": {"description": "Third question", "type": "string"},
            "required": False
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


@pytest.fixture
def mock_interview_action():
    """Create a mock interview action."""
    action = MagicMock()
    action.get_class_name = MagicMock(return_value="TestInterviewAction")
    return action


class TestQuestionWalker:
    """Test QuestionWalker functionality."""
    
    @pytest.mark.asyncio
    async def test_find_next_unanswered_question(self, test_session, mock_interview_action):
        """Test finding next unanswered question."""
        walker = QuestionWalker()
        walker.interview_session = test_session
        
        # No questions answered yet
        unanswered = test_session.get_unanswered_questions()
        assert "q1" in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered
        
        # Answer first question
        test_session.set_response("q1", "answer1")
        
        unanswered = test_session.get_unanswered_questions()
        assert "q1" not in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered
    
    @pytest.mark.asyncio
    async def test_get_answered_questions(self, test_session):
        """Test getting answered questions."""
        assert len(test_session.get_answered_questions()) == 0
        
        test_session.set_response("q1", "answer1")
        test_session.set_response("q2", "answer2")
        
        answered = test_session.get_answered_questions()
        assert "q1" in answered
        assert "q2" in answered
        assert len(answered) == 2
    
    @pytest.mark.asyncio
    async def test_get_unanswered_questions(self, test_session):
        """Test getting unanswered questions."""
        unanswered = test_session.get_unanswered_questions()
        assert len(unanswered) == 3
        assert "q1" in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered
        
        test_session.set_response("q1", "answer1")
        unanswered = test_session.get_unanswered_questions()
        assert "q1" not in unanswered
        assert len(unanswered) == 2
    
    @pytest.mark.asyncio
    async def test_get_required_questions(self, test_session):
        """Test getting required questions."""
        required = test_session.get_required_questions()
        assert "q1" in required
        assert "q2" in required
        assert "q3" not in required  # Optional
        assert len(required) == 2
    
    @pytest.mark.asyncio
    async def test_state_target_detection(self):
        """Test state target detection."""
        walker = QuestionWalker()
        
        assert walker._is_state_target("REVIEW") is True
        assert walker._is_state_target("COMPLETED") is True
        assert walker._is_state_target("CANCELLED") is True
        assert walker._is_state_target("ACTIVE") is False  # Not a state target
        assert walker._is_state_target("question_name") is False
    
    @pytest.mark.asyncio
    async def test_get_state_from_target(self):
        """Test getting InterviewState from target string."""
        walker = QuestionWalker()
        
        from jvagent.action.interview.core.foundation.enums import InterviewState
        
        assert walker._get_state_from_target("REVIEW") == InterviewState.REVIEW
        assert walker._get_state_from_target("COMPLETED") == InterviewState.COMPLETED
        assert walker._get_state_from_target("CANCELLED") == InterviewState.CANCELLED
        assert walker._get_state_from_target("invalid") is None
