"""ReviewStateInteractAction for review and confirmation phase."""

import logging
from typing import TYPE_CHECKING, Optional

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
from ..core.interview_walker import InterviewWalker as InterviewWalkerType
from ..core.validation import InterviewState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class ReviewStateInteractAction(InteractAction):
    """Review state action - presents summary and handles confirmation/editing.
    
    This action:
    - Formats collected responses as summary
    - Detects confirmation ("yes", "looks good")
    - Detects field-specific edits ("change name to...")
    - Routes edits back to ACTIVE with specific field target
    - Transitions to COMPLETED on confirmation
    - Detects cancellation
    """
    
    description: str = "Review state action for confirmation and editing phase"
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=True,
        description="Always execute when in REVIEW state",
    )
    
    confirmation_keywords: list = attribute(
        default_factory=lambda: ["yes", "correct", "looks good", "that's right", "confirm", "ok", "okay", "sounds good"],
        description="Keywords that indicate confirmation",
    )
    
    cancellation_keywords: list = attribute(
        default_factory=lambda: ["cancel", "abort", "stop", "nevermind", "forget it"],
        description="Keywords that indicate cancellation",
    )

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.debug("ReviewStateInteractAction registered")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute review state action.
        
        Args:
            visitor: The InteractWalker visiting this action
        """
        try:
            interaction = visitor.interaction
            if not interaction:
                logger.warning("ReviewStateInteractAction: No interaction available")
                return
            
            # Get session - try from InterviewWalker first, otherwise load it
            session = None
            if isinstance(visitor, InterviewWalkerType):
                session = visitor.interview_session
            
            if not session:
                try:
                    # Load session from conversation
                    conversation = await interaction.get_conversation()
                    if not conversation:
                        logger.warning("ReviewStateInteractAction: No conversation available")
                        return
                    
                    from ..core.interview_session import InterviewSession
                    sessions = await conversation.nodes(direction="out", node=InterviewSession)
                    if not sessions:
                        sessions = await InterviewSession.find({
                            "context.conversation_id": conversation.id
                        })
                    
                    if sessions:
                        active_sessions = [s for s in sessions if s.state != InterviewState.COMPLETED and s.state != InterviewState.CANCELLED]
                        if active_sessions:
                            session = active_sessions[0]
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to load session: {e}", exc_info=True)
                    return
            
            if not session:
                logger.warning("ReviewStateInteractAction: No session available")
                return
            
            # Only execute if session is in REVIEW state
            if session.state != InterviewState.REVIEW:
                logger.debug(f"ReviewStateInteractAction: Session is in {session.state} state, skipping")
                return
            
            # Check if this is the first time entering review (show summary)
            if not interaction.response or "summary" not in interaction.response.lower():
                try:
                    summary = self.format_summary(session)
                    directive = f"{summary}\n\nEverything correct? You can say 'yes', 'no', or edit a specific detail."
                    await self.respond_with_directive(visitor, directive)
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to show summary: {e}", exc_info=True)
                return
            
            # Parse user response
            utterance_lower = visitor.utterance.lower()
            
            # Check for cancellation
            if any(keyword in utterance_lower for keyword in self.cancellation_keywords):
                try:
                    session.transition_to(InterviewState.CANCELLED)
                    await session.save()
                    await self.respond_with_directive(visitor, "Interview cancelled. Let me know if you'd like to start over.")
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to handle cancellation: {e}", exc_info=True)
                return
            
            # Check for confirmation
            if any(keyword in utterance_lower for keyword in self.confirmation_keywords):
                try:
                    session.transition_to(InterviewState.COMPLETED)
                    await session.save()
                    await self.respond_with_directive(visitor, "Great! Your information has been saved. Thank you!")
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to handle confirmation: {e}", exc_info=True)
                return
            
            # Check for field-specific edit
            try:
                edit_field = await self.detect_edit_intent(visitor.utterance, session)
                if edit_field:
                    # Set active question and transition to ACTIVE
                    session.active_question_key = edit_field
                    session.transition_to(InterviewState.ACTIVE)
                    await session.save()
                    
                    # Get question config for directive
                    question_config = next(
                        (q for q in session.question_index if q.get("name") == edit_field),
                        None
                    )
                    
                    if question_config:
                        question_text = question_config.get("question", f"What is your {edit_field}?")
                        directive = f"Let's update {edit_field}. {question_text}"
                    else:
                        directive = f"Let's update {edit_field}. What would you like to change it to?"
                    
                    await self.respond_with_directive(visitor, directive)
                    return
            except Exception as e:
                logger.error(f"ReviewStateInteractAction: Failed to handle edit intent: {e}", exc_info=True)
                # Continue to unclear response handling
            
            # Unclear response, ask again
            try:
                directive = "I didn't understand. Please say 'yes' to confirm, 'no' to edit, or specify which field you'd like to change."
                await self.respond_with_directive(visitor, directive)
            except Exception as e:
                logger.error(f"ReviewStateInteractAction: Failed to respond with directive: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"ReviewStateInteractAction: Unexpected error in execute(): {e}", exc_info=True)
            raise  # Re-raise to let InteractWalker handle it

    def format_summary(self, session: InterviewSession) -> str:
        """Format collected responses as a summary.
        
        Args:
            session: Interview session
            
        Returns:
            Formatted summary string
        """
        lines = ["Here's what I have:\n"]
        
        for question_config in session.question_index:
            field_name = question_config.get("name", "")
            if not field_name:
                continue
            
            value = session.get_response(field_name)
            if value is None:
                continue
            
            # Format field name nicely
            display_name = field_name.replace("_", " ").title()
            lines.append(f"{display_name}: {value}")
        
        return "\n".join(lines)

    async def detect_edit_intent(
        self, 
        utterance: str, 
        session: InterviewSession
    ) -> Optional[str]:
        """Detect which field the user wants to edit.
        
        Args:
            utterance: User's utterance
            session: Interview session
            
        Returns:
            Field name if edit detected, None otherwise
        """
        utterance_lower = utterance.lower()
        answered_fields = session.get_answered_questions()
        
        # Check if any field name appears in utterance
        for field in answered_fields:
            field_display = field.replace("_", " ").lower()
            if field_display in utterance_lower or field in utterance_lower:
                # Check for edit keywords
                edit_keywords = ["change", "update", "edit", "modify", "instead", "actually", "correction"]
                if any(keyword in utterance_lower for keyword in edit_keywords):
                    return field
        
        return None

    async def respond_with_directive(
        self,
        visitor: "InteractWalker",
        directive: str
    ) -> None:
        """Respond with a directive.
        
        Args:
            visitor: InteractWalker
            directive: Directive string
        """
        if directive:
            await visitor.add_event("reviewing interview responses")
            await self.respond(
                visitor,
                directives=[directive],
                parameters=self.parameters if self.parameters else None
            )

