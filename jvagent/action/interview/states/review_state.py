"""ReviewStateInteractAction for review and confirmation phase."""

import logging
from typing import TYPE_CHECKING, Optional

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
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
        pass

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
            
            # Get session from visitor (set by parent interview action)
            session = getattr(visitor, 'interview_session', None)
            
            if not session:
                logger.warning(f"{self.get_class_name()}: No session available on visitor")
                return
            
            # Only execute if session is in REVIEW state
            if session.state != InterviewState.REVIEW:
                return
            
            # Check if this is the first time entering review (show summary)
            # Use session context to track if summary was shown
            summary_shown = session.context.get('review_summary_shown', False)
            
            if not summary_shown:
                try:
                    summary = self.format_summary(session)
                    directive = f"{summary}\n\nEverything correct? You can say 'yes', 'no', or edit a specific detail."
                    await self.respond_with_directive(visitor, directive)
                    # Mark summary as shown
                    session.context['review_summary_shown'] = True
                    await session.save()
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
                    
                    # Add CancelledStateInteractAction to walk path
                    from .cancelled_state import CancelledStateInteractAction
                    cancelled_action = await self.node(node=CancelledStateInteractAction)
                    if cancelled_action:
                        await visitor.add_next([cancelled_action])
                    
                    await self.respond_with_directive(visitor, "Interview cancelled. Let me know if you'd like to start over.")
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to handle cancellation: {e}", exc_info=True)
                return
            
            # Check for confirmation
            confirmation_detected = any(keyword in utterance_lower for keyword in self.confirmation_keywords)
            if confirmation_detected:
                try:
                    session.transition_to(InterviewState.COMPLETED)
                    await session.save()
                    
                    # Add CompletedStateInteractAction to walk path
                    from .completed_state import CompletedStateInteractAction
                    completed_action = await self.node(node=CompletedStateInteractAction)
                    if completed_action:
                        await visitor.add_next([completed_action])
                    else:
                        logger.warning("ReviewStateInteractAction: CompletedStateInteractAction not found")
                except Exception as e:
                    logger.error(f"ReviewStateInteractAction: Failed to handle confirmation: {e}", exc_info=True)
                return
            
            # Check for field-specific edit
            try:
                edit_field = await self.detect_edit_intent(visitor.utterance, session)
                if edit_field:
                    # Set active question and transition to ACTIVE
                    # Clear review summary flag so it can be shown again if needed
                    session.context.pop('review_summary_shown', None)
                    session.active_question_key = edit_field
                    session.transition_to(InterviewState.ACTIVE)
                    await session.save()
                    
                    # Add InterviewStateInteractAction to walk path to handle the edit
                    from .interview_state import InterviewStateInteractAction
                    interview_action = await self.node(node=InterviewStateInteractAction)
                    if interview_action:
                        await visitor.add_next([interview_action])
                    
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
            
            # Unclear response - ask for clarification
            try:
                # If we detected general edit intent but couldn't identify the field, ask which one
                general_edit_keywords = ["no", "not correct", "wrong", "incorrect", "edit", "change"]
                has_edit_intent = any(keyword in utterance_lower for keyword in general_edit_keywords)
                
                if has_edit_intent:
                    # User wants to edit but didn't specify which field
                    answered_fields = session.get_answered_questions()
                    field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
                    directive = f"Which field would you like to change? Available fields: {field_list}"
                else:
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
        
        Uses keyword matching to identify edit intent and field name.
        
        Args:
            utterance: User's utterance
            session: Interview session
            
        Returns:
            Field name if edit detected, None otherwise
        """
        utterance_lower = utterance.lower()
        answered_fields = session.get_answered_questions()
        
        # Check for general "no" or "edit" without specific field
        general_edit_keywords = ["no", "not correct", "wrong", "incorrect", "edit", "change", "update", "modify"]
        has_general_edit = any(keyword in utterance_lower for keyword in general_edit_keywords)
        
        # Check if any field name appears in utterance
        for field in answered_fields:
            field_display = field.replace("_", " ").lower()
            field_words = field_display.split()
            
            # Check if field name or parts of it appear in utterance
            field_mentioned = (
                field_display in utterance_lower or 
                field in utterance_lower or
                any(word in utterance_lower for word in field_words if len(word) > 2)
            )
            
            if field_mentioned:
                # Check for edit keywords
                edit_keywords = ["change", "update", "edit", "modify", "instead", "actually", "correction", "wrong", "incorrect"]
                if any(keyword in utterance_lower for keyword in edit_keywords):
                    return field
                # If field is mentioned and we have general edit intent, return that field
                if has_general_edit:
                    return field
        
        # If general edit intent but no specific field, return first field as default
        # (user can clarify which field they want to edit)
        if has_general_edit and answered_fields:
            return answered_fields[0]
        
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

