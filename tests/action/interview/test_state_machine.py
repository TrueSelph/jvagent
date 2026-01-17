"""Tests for interview state machine."""

import pytest
from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.foundation.exceptions import InvalidStateTransitionError
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.state.state_machine import InterviewStateMachine


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


class TestInterviewStateMachine:
    """Test state machine transitions and validation."""
    
    @pytest.mark.asyncio
    async def test_valid_transition_active_to_review(self, test_session):
        """Test valid transition from ACTIVE to REVIEW."""
        machine = InterviewStateMachine(test_session)
        
        assert machine.can_transition_to(InterviewState.REVIEW)
        result = machine.transition_to(InterviewState.REVIEW, reason="All questions answered")
        
        assert result is True
        assert test_session.state == InterviewState.REVIEW
        assert len(machine.get_transition_history()) == 1
    
    @pytest.mark.asyncio
    async def test_valid_transition_active_to_cancelled(self, test_session):
        """Test valid transition from ACTIVE to CANCELLED."""
        machine = InterviewStateMachine(test_session)
        
        assert machine.can_transition_to(InterviewState.CANCELLED)
        result = machine.transition_to(InterviewState.CANCELLED, reason="User cancellation")
        
        assert result is True
        assert test_session.state == InterviewState.CANCELLED
    
    @pytest.mark.asyncio
    async def test_valid_transition_review_to_active(self, test_session):
        """Test valid transition from REVIEW to ACTIVE."""
        test_session.state = InterviewState.REVIEW
        machine = InterviewStateMachine(test_session)
        
        assert machine.can_transition_to(InterviewState.ACTIVE)
        result = machine.transition_to(InterviewState.ACTIVE, reason="User wants to edit")
        
        assert result is True
        assert test_session.state == InterviewState.ACTIVE
    
    @pytest.mark.asyncio
    async def test_valid_transition_review_to_completed(self, test_session):
        """Test valid transition from REVIEW to COMPLETED."""
        test_session.state = InterviewState.REVIEW
        machine = InterviewStateMachine(test_session)
        
        assert machine.can_transition_to(InterviewState.COMPLETED)
        result = machine.transition_to(InterviewState.COMPLETED, reason="User confirmation")
        
        assert result is True
        assert test_session.state == InterviewState.COMPLETED
        assert test_session.completed_at is not None
    
    @pytest.mark.asyncio
    async def test_invalid_transition_active_to_completed(self, test_session):
        """Test invalid transition from ACTIVE to COMPLETED."""
        machine = InterviewStateMachine(test_session)
        
        assert not machine.can_transition_to(InterviewState.COMPLETED)
        
        with pytest.raises(ValueError) as exc_info:
            machine.transition_to(InterviewState.COMPLETED)
        
        assert "Invalid state transition" in str(exc_info.value)
        assert test_session.state == InterviewState.ACTIVE  # State unchanged
    
    @pytest.mark.asyncio
    async def test_invalid_transition_completed_to_active(self, test_session):
        """Test invalid transition from COMPLETED (terminal state)."""
        test_session.state = InterviewState.COMPLETED
        machine = InterviewStateMachine(test_session)
        
        assert not machine.can_transition_to(InterviewState.ACTIVE)
        
        with pytest.raises(ValueError):
            machine.transition_to(InterviewState.ACTIVE)
    
    @pytest.mark.asyncio
    async def test_invalid_transition_cancelled_to_active(self, test_session):
        """Test invalid transition from CANCELLED (terminal state)."""
        test_session.state = InterviewState.CANCELLED
        machine = InterviewStateMachine(test_session)
        
        assert not machine.can_transition_to(InterviewState.ACTIVE)
        
        with pytest.raises(ValueError):
            machine.transition_to(InterviewState.ACTIVE)
    
    @pytest.mark.asyncio
    async def test_transition_history(self, test_session):
        """Test that transition history is recorded."""
        machine = InterviewStateMachine(test_session)
        
        machine.transition_to(InterviewState.REVIEW, reason="All questions answered")
        machine.transition_to(InterviewState.COMPLETED, reason="User confirmed")
        
        history = machine.get_transition_history()
        assert len(history) == 2
        assert history[0]["from"] == InterviewState.ACTIVE.value
        assert history[0]["to"] == InterviewState.REVIEW.value
        assert history[0]["reason"] == "All questions answered"
        assert history[1]["from"] == InterviewState.REVIEW.value
        assert history[1]["to"] == InterviewState.COMPLETED.value
    
    @pytest.mark.asyncio
    async def test_get_valid_transitions(self, test_session):
        """Test getting valid transitions from current state."""
        machine = InterviewStateMachine(test_session)
        
        valid = machine.get_valid_transitions()
        assert InterviewState.REVIEW in valid
        assert InterviewState.CANCELLED in valid
        assert InterviewState.COMPLETED not in valid
        assert InterviewState.ACTIVE not in valid
        
        # Test from REVIEW state
        test_session.state = InterviewState.REVIEW
        machine = InterviewStateMachine(test_session)
        valid = machine.get_valid_transitions()
        assert InterviewState.ACTIVE in valid
        assert InterviewState.COMPLETED in valid
        assert InterviewState.CANCELLED in valid
