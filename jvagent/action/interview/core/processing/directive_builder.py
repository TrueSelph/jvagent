"""Directive builder for interview action.

Extracted directive formatting and generation logic from interview_interact_action.py
for better separation of concerns.
"""

import logging
from typing import TYPE_CHECKING, Optional

from ..foundation.enums import InterviewState
from ..session.interview_session import InterviewSession

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


class DirectiveBuilder:
    """Handles directive formatting and generation for interview actions."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize directive builder with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._event_added = False
    
    def reset_event_tracking(self) -> None:
        """Reset event tracking flag for new execution."""
        self._event_added = False
    
    def format_summary(self, session: InterviewSession) -> str:
        """Format collected responses as a summary.

        Args:
            session: Interview session

        Returns:
            Formatted summary string
        """
        lines = []
        templates = self.action.config.templates
        if templates.summary_header and templates.summary_header.strip():
            lines.append(templates.summary_header)

        for question_config in session.question_graph:
            field_name = question_config.get("name", "")
            if not field_name:
                continue

            value = session.get_response(field_name)
            if value is None:
                continue

            # Format field name nicely
            display_name = field_name.replace("_", " ").title()
            item = templates.summary_item.format(
                display_name=display_name,
                value=value
            )
            lines.append(item)

        return "\n".join(lines)

    def build_confirmation_directive(self, session: InterviewSession) -> str:
        """Build the complete confirmation directive.

        Args:
            session: Interview session

        Returns:
            Complete confirmation directive string
        """
        summary = self.format_summary(session)
        templates = self.action.config.templates

        # Use direct confirmation template
        return templates.review_confirmation.format(
            summary=summary,
            instructions=templates.confirmation_instructions,
            prompt=templates.confirmation_prompt,
        )
    
    async def queue_directive(
        self,
        visitor: "InteractWalker",
        directive: str
    ) -> None:
        """Queue a directive for later response generation.

        The event is determined automatically based on the session state and added only once
        per execution, even if multiple directives are queued.

        Args:
            visitor: InteractWalker
            directive: Directive string to queue
        """
        if directive and directive.strip():
            # Add event only once per execution, determined by session state
            if not self._event_added:
                # Determine event based on session state from visitor
                session = getattr(visitor, 'interview_session', None)
                if session:
                    if session.state == InterviewState.COMPLETED:
                        # Completion event is already added explicitly in generate_completed_directive
                        # Skip to avoid duplicate events
                        event_name = None
                    else:
                        # Use helper function to get state-specific event message
                        event_name = self.action.config.templates.get_state_event_message(
                            session.state.value,
                            self.action.get_class_name()
                        )
                else:
                    # No session available, default to active event
                    event_name = self.action.config.templates.get_state_event_message(
                        "ACTIVE",
                        self.action.get_class_name()
                    )

                # Only add event if one was determined (skip if COMPLETED state already handled)
                if event_name:
                    await visitor.add_event(event_name)
                    self._event_added = True
                else:
                    # Event already added explicitly, just mark as added
                    self._event_added = True

            await visitor.add_directive(directive)
        else:
            logger.warning(f"{self.action.get_class_name()}: Attempted to queue empty directive")
    
    async def generate_completed_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for COMPLETED state.

        Calls registered completion handler and cleans up session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        # Explicitly add completion event BEFORE cleaning up the session
        # This ensures the event is recorded even if the session is removed
        # Mark event as added to prevent queue_directive from adding it again
        completion_event = self.action.config.templates.get_state_event_message(
            "COMPLETED",
            self.action.get_class_name()
        )
        await visitor.add_event(completion_event)
        self._event_added = True  # Prevent duplicate event addition in queue_directive

        # Get completion handler for this interview type
        interview_type = session.interview_type
        completion_handler = self.action.get_completion_handler(interview_type)

        if completion_handler:
            try:
                await completion_handler(session, visitor, self.action)
                # Completion handler is responsible for sending its own message if needed
            except Exception as e:
                logger.error(
                    f"{self.action.get_class_name()}: Completion handler failed: {e}",
                    exc_info=True
                )
                # Send generic completion message on error
                await self.queue_directive(
                    visitor,
                    self.action.config.templates.completion_message
                )
        else:
            # No completion handler registered, send generic message
            await self.queue_directive(
                visitor,
                self.action.config.templates.completion_message
            )

        # Clean up and remove the session (always, regardless of handler success/failure)
        from ..utils.session_utils import cleanup_session
        await cleanup_session(session, visitor, self.action.get_class_name())

    async def generate_cancelled_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Sends cancellation acknowledgment and removes/clears the session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        # Explicitly add cancellation event BEFORE queue_directive and cleanup
        # This ensures the event is recorded even if the session is removed
        # Mark event as added to prevent queue_directive from adding it again
        cancellation_event = self.action.config.templates.get_state_event_message(
            "CANCELLED",
            self.action.get_class_name()
        )
        await visitor.add_event(cancellation_event)
        self._event_added = True  # Prevent duplicate event addition in queue_directive

        # Send cancellation message
        await self.queue_directive(
            visitor,
            self.action.config.templates.cancellation_message
        )

        # Clean up and remove the session
        from ..utils.session_utils import cleanup_session
        await cleanup_session(session, visitor, self.action.get_class_name())
