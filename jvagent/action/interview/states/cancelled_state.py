"""CancelledStateInteractAction for interview cancellation."""

import logging
from typing import TYPE_CHECKING

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
from ..core.interview_walker import InterviewWalker as InterviewWalkerType
from ..core.validation import InterviewState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class CancelledStateInteractAction(InteractAction):
    """Cancelled state action - handles interview cancellation.
    
    This action:
    - Marks session as cancelled
    - Optional cleanup/logging
    - Releases resources
    """
    
    description: str = "Cancelled state action for interview cancellation"
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=False,
        description="Only execute when interview is cancelled",
    )

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.debug("CancelledStateInteractAction registered")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute cancelled state action - handle cancellation.
        
        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("CancelledStateInteractAction: No interaction available")
            return
        
        # Get session - try from InterviewWalker first, otherwise load it
        session = None
        if isinstance(visitor, InterviewWalkerType):
            session = visitor.interview_session
        
        if not session:
            # Load session from conversation
            conversation = await interaction.get_conversation()
            if not conversation:
                logger.warning("CancelledStateInteractAction: No conversation available")
                return
            
            from ..core.interview_session import InterviewSession
            sessions = await conversation.nodes(direction="out", node=InterviewSession)
            if not sessions:
                sessions = await InterviewSession.find({
                    "context.conversation_id": conversation.id
                })
            
            if sessions:
                session = sessions[0]  # Get most recent
        
        if not session:
            logger.warning("CancelledStateInteractAction: No session available")
            return
        
        # Only execute if session is in CANCELLED state
        if session.state != InterviewState.CANCELLED:
            logger.debug(f"CancelledStateInteractAction: Session is in {session.state} state, skipping")
            return
        
        # Save state (already CANCELLED)
        await session.save()
        
        logger.debug(f"CancelledStateInteractAction: Cancelled session {session.id}")
        
        # Optional: Clean up or log cancellation
        # Session remains in database for audit purposes

