"""CompletedStateInteractAction for interview finalization."""

import logging
from typing import TYPE_CHECKING

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
from ..core.validation import InterviewState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class CompletedStateInteractAction(InteractAction):
    """Completed state action - handles post-completion.
    
    This action:
    - Finalizes session (state=COMPLETED, timestamp)
    - Optionally triggers downstream actions
    - Persists final responses
    - Cleans up session state
    
    Extensibility:
    - Override on_complete() to process collected data
    - Override get_completion_message() to customize response
    - Override should_cleanup_session() to control cleanup behavior
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
        """Execute completion logic."""
        # Load session from visitor (set by parent interview action)
        session = getattr(visitor, 'interview_session', None)
        if not session or session.state != InterviewState.COMPLETED:
            return
        
        # Check for decorator-registered completion handler
        from ..interview_interact_action import InterviewInteractAction
        
        completion_handler = None
        if session.interview_type:
            completion_handler = InterviewInteractAction.get_completion_handler(session.interview_type)
        
        if completion_handler:
            # Call decorator-registered handler
            try:
                await completion_handler(session, visitor)
            except Exception as e:
                logger.error(f"Error in completion handler for {session.interview_type}: {e}", exc_info=True)
        else:
            # Fallback to default behavior
            await self.on_complete(session, visitor)
        
        # Standard completion message
        message = await self.get_completion_message(session)
        await self.respond(visitor, directives=[message])
        
        # Cleanup if requested
        if await self.should_cleanup_session(session):
            await session.cleanup()
    
    async def on_complete(self, session: InterviewSession, visitor: "InteractWalker") -> None:
        """Hook for custom completion logic. Override in subclasses or use @on_interview_complete decorator."""
        pass
    
    async def get_completion_message(self, session: InterviewSession) -> str:
        """Get completion message. Override to customize."""
        return "Thank you! Your information has been recorded."
    
    async def should_cleanup_session(self, session: InterviewSession) -> bool:
        """Determine if session should be cleaned up. Override to customize."""
        return True  # Default: cleanup after completion

