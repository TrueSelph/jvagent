"""CompletedStateInteractAction for interview finalization."""

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


class CompletedStateInteractAction(InteractAction):
    """Completed state action - finalizes interview session.
    
    This action:
    - Finalizes session (state=COMPLETED, timestamp)
    - Optionally triggers downstream actions
    - Persists final responses
    - Cleans up session state
    """
    
    description: str = "Completed state action for interview finalization"
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=False,
        description="Only execute when interview is completed",
    )

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.debug("CompletedStateInteractAction registered")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute completed state action - finalize interview.
        
        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("CompletedStateInteractAction: No interaction available")
            return
        
        # Get session - try from InterviewWalker first, otherwise load it
        session = None
        if isinstance(visitor, InterviewWalkerType):
            session = visitor.interview_session
        
        if not session:
            # Load session from conversation
            conversation = await interaction.get_conversation()
            if not conversation:
                logger.warning("CompletedStateInteractAction: No conversation available")
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
            logger.warning("CompletedStateInteractAction: No session available")
            return
        
        # Only execute if session is in COMPLETED state
        if session.state != InterviewState.COMPLETED:
            logger.debug(f"CompletedStateInteractAction: Session is in {session.state} state, skipping")
            return
        
        # Save final state (already COMPLETED)
        await session.save()
        
        logger.debug(f"CompletedStateInteractAction: Finalized session {session.id}")
        
        # Optionally trigger downstream actions or persist to conversation
        # For now, responses are already stored in session.responses
        # They can be accessed via the session node

