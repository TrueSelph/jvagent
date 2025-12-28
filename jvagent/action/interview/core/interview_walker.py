"""InterviewWalker for traversing interview nodes.

This module provides the InterviewWalker that orchestrates state transitions
and routes to appropriate state actions based on the interview session state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.memory.conversation import Conversation
from jvspatial.core import on_visit
from jvspatial.core.annotations import attribute

from .interview_session import InterviewSession
from .question_node import QuestionNode
from .validation import InterviewState

if TYPE_CHECKING:
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction
    from jvagent.action.interview.states.active_state import ActiveStateInteractAction
    from jvagent.action.interview.states.review_state import ReviewStateInteractAction
    from jvagent.action.interview.states.completed_state import CompletedStateInteractAction
    from jvagent.action.interview.states.cancelled_state import CancelledStateInteractAction

logger = logging.getLogger(__name__)


class InterviewWalker(InteractWalker):
    """Walker that orchestrates interview state transitions.
    
    This walker:
    - Loads or creates InterviewSession from conversation
    - Routes to appropriate state action based on session.state
    - Traverses QuestionNode chains during ACTIVE state
    - Handles state transitions and persistence
    """
    
    conversation: Conversation = attribute(
        default=None,
        description="Conversation node containing the interview session",
    )
    
    directive: str = attribute(
        default_factory=str,
        description="Directive to be executed",
    )
    
    interview_session: Optional[InterviewSession] = attribute(
        default=None,
        description="Current interview session node",
    )

    async def load_or_create_session(
        self, 
        interview_action: "InterviewInteractAction"
    ) -> Optional[InterviewSession]:
        """Load existing InterviewSession from conversation or create new one.
        
        Args:
            interview_action: The root InterviewInteractAction
            
        Returns:
            InterviewSession node if conversation available, None otherwise
        """
        try:
            if not self.conversation:
                logger.warning("InterviewWalker: No conversation available")
                return None
            
            # Try to find existing session connected to this conversation
            try:
                # First try to find via edge connection
                sessions = await self.conversation.nodes(direction="out", node=InterviewSession)
                
                # If not found, try query by context
                if not sessions:
                    sessions = await InterviewSession.find({
                        "context.conversation_id": self.conversation.id
                    })
            except Exception as e:
                logger.error(f"InterviewWalker: Failed to query for existing sessions: {e}", exc_info=True)
                sessions = []
            
            if sessions:
                # Use the most recent active session
                active_sessions = [s for s in sessions if s.state != InterviewState.COMPLETED and s.state != InterviewState.CANCELLED]
                if active_sessions:
                    session = active_sessions[0]
                    logger.debug(f"InterviewWalker: Loaded existing session {session.id} in state {session.state}")
                    self.interview_session = session
                    return session
            
            # Create new session if none exists (starts in ACTIVE state)
            try:
                session = await InterviewSession.create(
                    agent_id=interview_action.agent_id,
                    conversation_id=self.conversation.id,
                    question_index=interview_action.state_index,
                    state=InterviewState.ACTIVE,
                )
                
                # Set started_at timestamp
                if not session.started_at:
                    from datetime import datetime
                    session.started_at = datetime.now()
                
                # conversation_id is already set via the create() call, just save
                await session.save()
                
                # Connect session to conversation
                await self.conversation.connect(session)
                
                logger.debug(f"InterviewWalker: Created new session {session.id}")
                self.interview_session = session
                return session
            except Exception as e:
                logger.error(f"InterviewWalker: Failed to create new session: {e}", exc_info=True)
                return None
        
        except Exception as e:
            logger.error(f"InterviewWalker: Unexpected error in load_or_create_session(): {e}", exc_info=True)
            return None

    @on_visit("InterviewInteractAction")
    async def on_interview_action(self, here) -> None:
        """Visit the root InterviewInteractAction and route to state-specific action.
        
        Args:
            here: The InterviewInteractAction being visited
        """
        logger.debug(f"InterviewWalker: On InterviewInteractAction")
        
        # Load or create session
        session = await self.load_or_create_session(here)
        if not session:
            logger.warning("InterviewWalker: Could not load or create session")
            return
        
        # Route to state-specific action based on session state
        # If IDLE (legacy state), transition to ACTIVE
        if session.state == InterviewState.IDLE:
            session.transition_to(InterviewState.ACTIVE)
            if not session.started_at:
                from datetime import datetime
                session.started_at = datetime.now()
            await session.save()
            logger.debug(f"InterviewWalker: Transitioned IDLE session to ACTIVE")
        
        if session.state == InterviewState.ACTIVE:
            active_action = await here.node(node="ActiveStateInteractAction")
            if active_action:
                await self.visit(active_action)
        elif session.state == InterviewState.REVIEW:
            review_action = await here.node(node="ReviewStateInteractAction")
            if review_action:
                await self.visit(review_action)
        elif session.state == InterviewState.COMPLETED:
            completed_action = await here.node(node="CompletedStateInteractAction")
            if completed_action:
                await self.visit(completed_action)
        elif session.state == InterviewState.CANCELLED:
            cancelled_action = await here.node(node="CancelledStateInteractAction")
            if cancelled_action:
                await self.visit(cancelled_action)
        else:
            logger.warning(f"InterviewWalker: Unknown state {session.state}")

    @on_visit("ActiveStateInteractAction")
    async def on_active_state_action(self, here) -> None:
        """Visit ActiveStateInteractAction and traverse QuestionNodes.
        
        Args:
            here: The ActiveStateInteractAction being visited
        """
        logger.debug(f"InterviewWalker: On ActiveStateInteractAction")
        
        if not self.interview_session:
            logger.warning("InterviewWalker: No session available for ActiveState")
            return
        
        # Find first unanswered question node
        question_node = await here.node(node="QuestionNode")
        if question_node:
            await self.visit(question_node)

    @on_visit(QuestionNode)
    async def on_question_node(self, here: QuestionNode) -> None:
        """Visit a QuestionNode and continue to next if needed.
        
        Args:
            here: The QuestionNode being visited
        """
        logger.debug(f"InterviewWalker: On QuestionNode {here.label}")
        
        directive = await here.execute(self)
        if directive:
            self.directive = directive
            return
        
        # Move to next question node
        next_node = await here.node(node="QuestionNode")
        if next_node:
            logger.debug(f"InterviewWalker: Visiting next QuestionNode: {next_node.label}")
            await self.visit(next_node)
        return

