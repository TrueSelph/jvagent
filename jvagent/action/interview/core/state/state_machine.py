"""State machine for interview action.

This module provides explicit state management with defined transitions.
"""

import logging
from typing import Dict, List, Optional, Set

from ..session.interview_session import InterviewSession
from ..foundation.enums import InterviewState
from ..foundation.exceptions import InvalidStateTransitionError

logger = logging.getLogger(__name__)


class InterviewStateMachine:
    """Explicit state machine for interview sessions.
    
    Defines valid state transitions and provides transition validation.
    Tracks state history for debugging purposes.
    """
    
    # Valid state transitions: {from_state: {to_states}}
    VALID_TRANSITIONS: Dict[InterviewState, Set[InterviewState]] = {
        InterviewState.ACTIVE: {
            InterviewState.REVIEW,      # All questions answered
            InterviewState.CANCELLED,   # User cancels
        },
        InterviewState.REVIEW: {
            InterviewState.ACTIVE,      # User wants to edit
            InterviewState.COMPLETED,   # User confirms
            InterviewState.CANCELLED,   # User cancels
        },
        InterviewState.COMPLETED: set(),  # Terminal state
        InterviewState.CANCELLED: set(),  # Terminal state
    }
    
    def __init__(self, session: InterviewSession):
        """Initialize state machine with session.
        
        Args:
            session: Interview session
        """
        self.session = session
        self._transition_history: List[Dict[str, any]] = []
    
    def can_transition_to(self, new_state: InterviewState) -> bool:
        """Check if transition to new_state is valid.
        
        Args:
            new_state: Target state
            
        Returns:
            True if transition is valid, False otherwise
        """
        current_state = self.session.state
        valid_targets = self.VALID_TRANSITIONS.get(current_state, set())
        return new_state in valid_targets
    
    def transition_to(
        self,
        new_state: InterviewState,
        reason: Optional[str] = None
    ) -> bool:
        """Transition to a new state if valid.
        
        Args:
            new_state: Target state
            reason: Optional reason for transition (for debugging)
            
        Returns:
            True if transition succeeded, False if invalid
            
        Raises:
            ValueError: If transition is invalid (only in strict mode)
        """
        current_state = self.session.state
        
        # Check if transition is valid
        if not self.can_transition_to(new_state):
            valid_transitions = [s.value for s in self.VALID_TRANSITIONS.get(current_state, set())]
            raise InvalidStateTransitionError(
                current_state.value,
                new_state.value,
                f"Valid transitions from {current_state.value}: {valid_transitions}"
            )
        
        # Record transition in history
        self._transition_history.append({
            "from": current_state.value,
            "to": new_state.value,
            "reason": reason,
            "timestamp": self.session.started_at if hasattr(self.session, 'started_at') else None
        })
        
        # Perform transition
        self.session.transition_to(new_state)
        
        logger.debug(
            f"State transition: {current_state.value} -> {new_state.value}"
            + (f" (reason: {reason})" if reason else "")
        )
        
        return True
    
    def get_transition_history(self) -> List[Dict[str, any]]:
        """Get state transition history.
        
        Returns:
            List of transition records
        """
        return self._transition_history.copy()
    
    def get_valid_transitions(self) -> Set[InterviewState]:
        """Get valid transitions from current state.
        
        Returns:
            Set of valid target states
        """
        return self.VALID_TRANSITIONS.get(self.session.state, set()).copy()
