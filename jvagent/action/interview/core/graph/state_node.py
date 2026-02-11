"""StateNode for interview state transitions.

This module provides StateNode, a node that represents interview state transition points
in the question graph (REVIEW, COMPLETED, CANCELLED).

StateNodes are first-class graph citizens following the data-spatial pattern. When visited
by a QuestionWalker, they:
1. Execute state transitions via the state machine
2. Generate state-specific directives
3. Trigger registered handlers (e.g., completion handlers for COMPLETED state)
"""

import logging
from typing import TYPE_CHECKING, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from ..foundation.enums import InterviewState
from ..state.state_machine import InterviewStateMachine

if TYPE_CHECKING:
    from ..graph.question_walker import QuestionWalker

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
    
    agent_id: str = attribute(
        default=None,
        description="ID of the agent this question belongs to"
    )
        
    interview_type: str = attribute(
        default=None,
        description="Type of interview this question belongs to (e.g., 'SignupInterviewInteractAction')"
    )
        
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
    
    async def execute(self, walker: "QuestionWalker") -> Optional[str]:
        """Execute state node to trigger state transition and return directive.

        Follows the same pattern as QuestionNode.execute():
        1. Performs state transition via the state machine
        2. Triggers any registered handlers (e.g., completion handler for COMPLETED)
        3. Returns the appropriate directive string

        Args:
            walker: QuestionWalker visiting this node (provides access to session,
                   interact_visitor, and interview_action)

        Returns:
            Directive string for the state, or None if no directive needed
        """
        session = walker.interview_session
        if not session:
            return None

        # Perform state transition if needed
        if session.state != self.state_type:
            logger.debug(
                f"StateNode: Transitioning session from {session.state.value} to {self.state_type.value}"
            )
            state_machine = InterviewStateMachine(session)
            try:
                state_machine.transition_to(self.state_type, reason=f"StateNode execution: {self.label}")
                await session.save()
            except ValueError as e:
                logger.error(f"StateNode: Invalid state transition: {e}", exc_info=True)
                raise

        # Generate state-specific directive and trigger handlers
        interview_action = walker.interview_action
        if not interview_action or not hasattr(interview_action, 'directive_builder'):
            logger.warning(
                f"StateNode.execute: No interview_action or directive_builder available"
            )
            return None

        builder = interview_action.directive_builder
        visitor = walker.interact_visitor

        if self.state_type == InterviewState.REVIEW:
            # Build and return confirmation directive
            return await builder.build_confirmation_directive(
                session,
                visitor=visitor,
                interview_action=interview_action,
            )

        elif self.state_type == InterviewState.COMPLETED:
            # COMPLETED state: trigger completion handler and cleanup
            # generate_completed_directive handles:
            # - Adding completion event to visitor
            # - Calling registered completion handler
            # - Queueing any handler-generated directives to visitor
            # - Session cleanup
            await builder.generate_completed_directive(session, visitor)
            # Directives are queued directly to visitor by generate_completed_directive
            return None

        elif self.state_type == InterviewState.CANCELLED:
            # CANCELLED state: handle cancellation and cleanup
            # generate_cancelled_directive handles:
            # - Adding cancellation event to visitor
            # - Queueing cancellation message to visitor
            # - Session cleanup
            await builder.generate_cancelled_directive(session, visitor)
            # Directives are queued directly to visitor by generate_cancelled_directive
            return None

        return None
    
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
