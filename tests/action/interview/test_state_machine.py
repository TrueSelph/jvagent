"""Tests for interview state transitions via StateNode."""

import pytest
from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.foundation.exceptions import InvalidStateTransitionError
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.state_node import StateNode


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_index=[],
        state=InterviewState.ACTIVE,
    )
    return session


class TestStateNodeTransitions:
    """Test state transitions via StateNode."""
    
    @pytest.mark.asyncio
    async def test_valid_transition_active_to_review(self, test_session):
        """Test valid transition from ACTIVE to REVIEW."""
        assert StateNode.can_transition(test_session.state, InterviewState.REVIEW)
        
        # Perform transition
        test_session.transition_to(InterviewState.REVIEW)
        
        assert test_session.state == InterviewState.REVIEW
    
    @pytest.mark.asyncio
    async def test_valid_transition_active_to_cancelled(self, test_session):
        """Test valid transition from ACTIVE to CANCELLED."""
        assert StateNode.can_transition(test_session.state, InterviewState.CANCELLED)
        
        test_session.transition_to(InterviewState.CANCELLED)
        
        assert test_session.state == InterviewState.CANCELLED
    
    @pytest.mark.asyncio
    async def test_valid_transition_review_to_active(self, test_session):
        """Test valid transition from REVIEW to ACTIVE."""
        test_session.state = InterviewState.REVIEW
        
        assert StateNode.can_transition(test_session.state, InterviewState.ACTIVE)
        test_session.transition_to(InterviewState.ACTIVE)
        
        assert test_session.state == InterviewState.ACTIVE
    
    @pytest.mark.asyncio
    async def test_valid_transition_review_to_completed(self, test_session):
        """Test valid transition from REVIEW to COMPLETED."""
        test_session.state = InterviewState.REVIEW
        
        assert StateNode.can_transition(test_session.state, InterviewState.COMPLETED)
        test_session.transition_to(InterviewState.COMPLETED)
        
        assert test_session.state == InterviewState.COMPLETED
        assert test_session.completed_at is not None
    
    @pytest.mark.asyncio
    async def test_invalid_transition_active_to_completed(self, test_session):
        """Test invalid transition from ACTIVE to COMPLETED."""
        assert not StateNode.can_transition(test_session.state, InterviewState.COMPLETED)
        
        # StateNode.execute() would raise InvalidStateTransitionError
        # For this test, we just verify can_transition returns False
        assert test_session.state == InterviewState.ACTIVE  # State unchanged
    
    @pytest.mark.asyncio
    async def test_invalid_transition_completed_to_active(self, test_session):
        """Test invalid transition from COMPLETED (terminal state)."""
        test_session.state = InterviewState.COMPLETED
        
        assert not StateNode.can_transition(test_session.state, InterviewState.ACTIVE)
    
    @pytest.mark.asyncio
    async def test_invalid_transition_cancelled_to_active(self, test_session):
        """Test invalid transition from CANCELLED (terminal state)."""
        test_session.state = InterviewState.CANCELLED
        
        assert not StateNode.can_transition(test_session.state, InterviewState.ACTIVE)
    
    @pytest.mark.asyncio
    async def test_multiple_transitions(self, test_session):
        """Test multiple valid transitions."""
        # ACTIVE -> REVIEW
        assert StateNode.can_transition(test_session.state, InterviewState.REVIEW)
        test_session.transition_to(InterviewState.REVIEW)
        assert test_session.state == InterviewState.REVIEW
        
        # REVIEW -> COMPLETED
        assert StateNode.can_transition(test_session.state, InterviewState.COMPLETED)
        test_session.transition_to(InterviewState.COMPLETED)
        assert test_session.state == InterviewState.COMPLETED
    
    @pytest.mark.asyncio
    async def test_get_valid_transitions(self, test_session):
        """Test getting valid transitions from current state."""
        valid = StateNode.get_valid_transitions(test_session.state)
        assert InterviewState.REVIEW in valid
        assert InterviewState.CANCELLED in valid
        assert InterviewState.COMPLETED not in valid
        assert InterviewState.ACTIVE not in valid
        
        # Test from REVIEW state
        test_session.state = InterviewState.REVIEW
        valid = StateNode.get_valid_transitions(test_session.state)
        assert InterviewState.ACTIVE in valid
        assert InterviewState.COMPLETED in valid
        assert InterviewState.CANCELLED in valid
