"""CancelledStateInteractAction for interview cancellation."""

import logging
from typing import TYPE_CHECKING

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
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
        
        # Get session from visitor (set by parent interview action)
        session = getattr(visitor, 'interview_session', None)
        
        if not session:
            logger.warning(f"{self.get_class_name()}: No session available on visitor")
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

