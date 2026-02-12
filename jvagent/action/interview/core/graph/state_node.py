"""StateNode for interview state transitions.

This module provides StateNode, a node that represents interview state transition points
in the question graph (REVIEW, COMPLETED, CANCELLED).

StateNodes are first-class graph citizens following the data-spatial pattern. When visited
by an InterviewWalker, they:
1. Execute state transitions with validation
2. Generate state-specific directives
3. Trigger registered handlers (e.g., completion handlers for COMPLETED state)
"""

import logging
from typing import TYPE_CHECKING, ClassVar, Dict, Optional, Set

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from ..foundation.enums import InterviewState
from ..foundation.exceptions import InvalidStateTransitionError

if TYPE_CHECKING:
    from ...interview_interact_action import InterviewInteractAction
    from ..graph.interview_walker import InterviewWalker
    from ..session.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class StateNode(Node):
    """Node representing an interview state transition point in the question graph.
    
    StateNodes are first-class graph citizens that represent interview states
    (REVIEW, COMPLETED, CANCELLED). They can be targets of conditional branches
    from questions and can have outgoing edges for state-to-state transitions
    or re-entry into the question flow.
    
    StateNode also manages state transition validation, replacing the need for
    a separate InterviewStateMachine class.
    
    Attributes:
        state_type: The InterviewState this node represents
        label: Human-readable label (typically the state name in uppercase)
    """
    
    description: str = "Interview state transition node"
    
    # Valid state transitions: {from_state: {to_states}}
    VALID_TRANSITIONS: ClassVar[Dict[InterviewState, Set[InterviewState]]] = {
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
    
    @classmethod
    def can_transition(cls, from_state: InterviewState, to_state: InterviewState) -> bool:
        """Check if transition from from_state to to_state is valid.
        
        Args:
            from_state: Current state
            to_state: Target state
            
        Returns:
            True if transition is valid, False otherwise
        """
        valid_targets = cls.VALID_TRANSITIONS.get(from_state, set())
        return to_state in valid_targets
    
    @classmethod
    def get_valid_transitions(cls, from_state: InterviewState) -> Set[InterviewState]:
        """Get valid transitions from a given state.
        
        Args:
            from_state: Current state
            
        Returns:
            Set of valid target states
        """
        return cls.VALID_TRANSITIONS.get(from_state, set()).copy()
    
    async def execute(self, walker: "InterviewWalker") -> Optional[str]:
        """Execute state node to trigger state transition and return directive.

        Follows the same pattern as QuestionNode.execute():
        1. Performs state transition via the state machine
        2. Triggers any registered handlers (e.g., completion handler for COMPLETED)
        3. Returns the appropriate directive string

        Args:
            walker: InterviewWalker visiting this node (provides access to session,
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
            
            # Validate transition
            if not self.can_transition(session.state, self.state_type):
                valid_transitions = [s.value for s in self.VALID_TRANSITIONS.get(session.state, set())]
                error = InvalidStateTransitionError(
                    session.state.value,
                    self.state_type.value,
                    f"Valid transitions from {session.state.value}: {valid_transitions}"
                )
                logger.error(f"StateNode: Invalid state transition: {error}", exc_info=True)
                raise error
            
            # Perform transition
            session.transition_to(self.state_type)
            await session.save()
            
            logger.debug(
                f"State transition: {session.state.value} -> {self.state_type.value} "
                f"(reason: StateNode execution: {self.label})"
            )

        # CANCELLED: Always remove session completely when this state is traversed.
        # Must run before the directive_builder guard so deletion happens even when
        # interview_action/directive_builder is unavailable.
        if self.state_type == InterviewState.CANCELLED:
            interview_action = walker.interview_action
            visitor = walker.interact_visitor
            if interview_action and hasattr(interview_action, "directive_builder"):
                await self.generate_cancelled_directive(
                    session, visitor, interview_action=interview_action
                )
            else:
                from ..utils.session_utils import cleanup_session
                action_name = "InterviewAction"
                if interview_action and hasattr(interview_action, "get_class_name"):
                    action_name = interview_action.get_class_name()
                await cleanup_session(session, visitor, action_name)
            return None

        # Generate state-specific directive and trigger handlers
        interview_action = walker.interview_action
        if not interview_action or not hasattr(interview_action, "directive_builder"):
            logger.warning(
                "StateNode.execute: No interview_action or directive_builder available"
            )
            return None

        visitor = walker.interact_visitor

        if self.state_type == InterviewState.REVIEW:
            return await self.build_confirmation_directive(
                session, visitor=visitor, interview_action=interview_action
            )

        elif self.state_type == InterviewState.COMPLETED:
            await self.generate_completed_directive(
                session, visitor, interview_action=interview_action
            )
            return None

        return None

    async def format_summary(
        self,
        session: "InterviewSession",
        visitor: Optional["InteractWalker"] = None,
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> str:
        """Format collected responses as a summary for REVIEW context.

        Delegates to the action's DirectiveBuilder. Use from REVIEW state or when
        building review-related directives (e.g. UPDATE unclear-which-field).

        Args:
            session: Interview session
            visitor: Optional InteractWalker for review override context
            interview_action: Interview action (must have directive_builder)

        Returns:
            Formatted summary string
        """
        if not interview_action or not hasattr(interview_action, "directive_builder"):
            return ""
        return await interview_action.directive_builder.format_summary(
            session, visitor=visitor, interview_action=interview_action
        )

    async def build_confirmation_directive(
        self,
        session: "InterviewSession",
        visitor: Optional["InteractWalker"] = None,
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> str:
        """Build the complete confirmation directive for REVIEW state.

        Delegates to the action's DirectiveBuilder. Used when executing the REVIEW
        state node to present the summary and confirmation prompt.

        Args:
            session: Interview session
            visitor: Optional InteractWalker for review override context
            interview_action: Interview action (must have directive_builder)

        Returns:
            Complete confirmation directive string
        """
        if not interview_action or not hasattr(interview_action, "directive_builder"):
            return ""
        return await interview_action.directive_builder.build_confirmation_directive(
            session, visitor=visitor, interview_action=interview_action
        )

    async def generate_completed_directive(
        self,
        session: "InterviewSession",
        visitor: "InteractWalker",
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> None:
        """Generate COMPLETED state directive: completion event, handler, queue directives, cleanup.

        Delegates to the action's DirectiveBuilder. Used when executing the COMPLETED
        state node.

        Args:
            session: Interview session
            visitor: InteractWalker to queue directives and add events
            interview_action: Interview action (must have directive_builder)
        """
        if interview_action and hasattr(interview_action, "directive_builder"):
            await interview_action.directive_builder.generate_completed_directive(
                session, visitor
            )

    async def generate_cancelled_directive(
        self,
        session: "InterviewSession",
        visitor: "InteractWalker",
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> None:
        """Generate CANCELLED state directive: cancellation event, message, cleanup.

        Delegates to the action's DirectiveBuilder. Used when executing the CANCELLED
        state node.

        Args:
            session: Interview session
            visitor: InteractWalker to queue directives and add events
            interview_action: Interview action (must have directive_builder)
        """
        if interview_action and hasattr(interview_action, "directive_builder"):
            await interview_action.directive_builder.generate_cancelled_directive(
                session, visitor
            )

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
