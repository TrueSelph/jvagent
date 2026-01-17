"""StateNode for interview state transitions.

This module provides StateNode, a node that represents interview state transition points
in the question graph (REVIEW, COMPLETED, CANCELLED).
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from .enums import InterviewState

if TYPE_CHECKING:
    from .interview_session import InterviewSession

logger = logging.getLogger(__name__)


class StateNode(Node):
    """Node representing an interview state transition point in the question graph.
    
    StateNodes are first-class graph citizens that represent interview states
    (REVIEW, COMPLETED, CANCELLED). They can be targets of conditional branches
    from questions and can have outgoing edges for state-to-state transitions
    or re-entry into the question flow.
    
    Attributes:
        state_type: The InterviewState this node represents
        label: Human-readable label (typically the state name in uppercase)
    """
    
    description: str = "Interview state transition node"
    
    state_type: InterviewState = attribute(
        default=InterviewState.ACTIVE,
        description="The interview state this node represents (REVIEW, COMPLETED, CANCELLED)"
    )
    
    label: str = attribute(
        default_factory=str,
        description="Human-readable label for the node (typically state name in uppercase)"
    )
    
    async def on_register(self) -> None:
        """Register the state node."""
        pass
    
    async def execute(
        self,
        session: "InterviewSession",
        visitor: Optional[Any] = None
    ) -> None:
        """Execute state node to trigger state transition.
        
        Transitions the interview session to the state represented by this node.
        This is called when a question branch leads to a state node.
        
        Args:
            session: Interview session to transition
            visitor: Optional walker for context
        """
        if session.state != self.state_type:
            logger.debug(
                f"StateNode: Transitioning session from {session.state.value} to {self.state_type.value}"
            )
            session.transition_to(self.state_type)
            await session.save()
    
    def is_terminal(self) -> bool:
        """Check if this state is terminal (no outgoing transitions allowed).
        
        COMPLETED and CANCELLED are terminal states. REVIEW can have outgoing
        edges back to questions for editing.
        
        Returns:
            True if state is terminal, False otherwise
        """
        return self.state_type in (InterviewState.COMPLETED, InterviewState.CANCELLED)
    
    def allows_reentry(self) -> bool:
        """Check if this state allows re-entry into question flow.
        
        REVIEW state allows re-entry for editing. COMPLETED and CANCELLED do not.
        
        Returns:
            True if state allows re-entry, False otherwise
        """
        return self.state_type == InterviewState.REVIEW
