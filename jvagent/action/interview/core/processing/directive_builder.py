"""Directive builder for interview action.

Extracted directive formatting and generation logic from interview_interact_action.py
for better separation of concerns.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..foundation.enums import InterviewState
from ..foundation.prompts import ACTIVE_TASK_DESCRIPTION_TEMPLATE
from ..session.interview_session import InterviewSession
from ..utils.handler_utils import invoke_async_with_optional_context

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

logger = logging.getLogger(__name__)


class DirectiveBuilder:
    """Handles directive formatting and generation for interview actions."""

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize directive builder with action instance.

        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._task_added = False

    def reset_task_tracking(self) -> None:
        """Reset task tracking flag for new execution."""
        self._task_added = False

    def _build_review_data(self, session: InterviewSession) -> Dict[str, Any]:
        """Build key-value dict of collected interview data from session (display only)."""
        data: Dict[str, Any] = {}
        for question_config in session.question_graph:
            field_name = question_config.get("name", "")
            if not field_name:
                continue
            value = session.get_response(field_name)
            if value is None:
                continue
            data[field_name] = value
        return data

    async def format_summary(
        self,
        session: InterviewSession,
        visitor: Optional["InteractWalker"] = None,
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> str:
        """Format collected responses as a summary.

        If an @input_review_override is registered, it is called with (session, copy of data)
        and optionally (visitor, interview_action) when the override accepts them.
        Session storage is never modified.

        Args:
            session: Interview session
            visitor: Optional InteractWalker for override context
            interview_action: Optional InterviewInteractAction for override context

        Returns:
            Formatted summary string
        """
        action = interview_action or self.action
        data = self._build_review_data(session)

        override = action.get_input_review_override()
        if override:
            result = await invoke_async_with_optional_context(
                override,
                session,
                dict(data),
                visitor=visitor,
                interview_action=interview_action or self.action,
            )
            data = result if result is not None else data

        lines: List[str] = []
        if action.summary_header and action.summary_header.strip():
            lines.append(action.summary_header)
        for field_name, value in data.items():
            display_name = field_name.replace("_", " ").title()
            line = action.summary_item.format(
                display_name=display_name,
                value=value,
            )
            lines.append(line)
        return "\n".join(lines)

    async def build_confirmation_directive(
        self,
        session: InterviewSession,
        visitor: Optional["InteractWalker"] = None,
        interview_action: Optional["InterviewInteractAction"] = None,
    ) -> str:
        """Build the complete confirmation directive.

        Calls registered review handler if available, then builds the confirmation directive.
        The review handler can return a custom prefix to prepend to the review summary.

        Args:
            session: Interview session
            visitor: Optional InteractWalker for review override context
            interview_action: Optional InterviewInteractAction for review override context

        Returns:
            Complete confirmation directive string
        """
        action = interview_action or self.action

        # Call review handler if registered
        interview_type = session.interview_type
        review_handler = action.get_review_handler(interview_type)
        custom_prefix = None

        if review_handler and visitor:
            try:
                custom_prefix = await invoke_async_with_optional_context(
                    review_handler, session, visitor=visitor, interview_action=action
                )
            except Exception as e:
                logger.error(
                    f"{action.get_class_name()}: Review handler failed: {e}",
                    exc_info=True,
                )
                # Continue with default behavior on error

        summary = await self.format_summary(
            session,
            visitor=visitor,
            interview_action=action,
        )

        # Build confirmation directive
        confirmation = action.review_confirmation.format(
            summary=summary,
            instructions=action.confirmation_instructions,
            prompt=action.confirmation_prompt,
        )

        # Prepend custom prefix if provided by review handler
        if custom_prefix:
            return f"{custom_prefix}\n\n{confirmation}"

        return confirmation

    async def queue_directive(self, visitor: "InteractWalker", directive: str) -> None:
        """Queue a directive for later response generation.

        Registers active task in task tracker when session is ACTIVE or REVIEW (once per
        execution). COMPLETED/CANCELLED events are added explicitly in their handlers.

        Args:
            visitor: InteractWalker
            directive: Directive string to queue
        """
        if directive and directive.strip():
            # Register active task only once per execution when session is ACTIVE or REVIEW
            if not self._task_added:
                session = getattr(visitor, "interview_session", None)
                if session:
                    if session.state in (InterviewState.ACTIVE, InterviewState.REVIEW):
                        action_name = self.action.get_class_name()
                        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
                            action_name=action_name
                        )
                        metadata = {
                            "interview_type": session.interview_type,
                            "state": session.state.value,
                        }
                        await visitor.add_active_task(
                            description=description,
                            metadata=metadata,
                            action_name=action_name,
                            task_type="INTERVIEW",
                        )
                else:
                    # No session available, default to active task
                    action_name = self.action.get_class_name()
                    description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
                        action_name=action_name
                    )
                    await visitor.add_active_task(
                        description=description,
                        action_name=action_name,
                        task_type="INTERVIEW",
                    )
                self._task_added = True

            await visitor.add_directive(directive)
        else:
            logger.warning(
                f"{self.action.get_class_name()}: Attempted to queue empty directive"
            )

    async def generate_completed_directive(
        self, session: InterviewSession, visitor: "InteractWalker"
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
        completion_event = self.action.get_state_event_message("COMPLETED")
        await visitor.add_event(completion_event)
        self._task_added = True  # Prevent duplicate task addition in queue_directive

        # Update task to completed (preserves task for audit log)
        action_name = self.action.get_class_name()
        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(action_name=action_name)
        await visitor.update_task(
            status="completed",
            description=description,
            action_name=action_name,
        )

        # Get completion handler for this interview type
        interview_type = session.interview_type
        completion_handler = self.action.get_completion_handler(interview_type)

        if completion_handler:
            try:
                await invoke_async_with_optional_context(
                    completion_handler,
                    session,
                    visitor=visitor,
                    interview_action=self.action,
                )
                # Completion handler is responsible for sending its own message if needed
            except Exception as e:
                logger.error(
                    f"{self.action.get_class_name()}: Completion handler failed: {e}",
                    exc_info=True,
                )
                # Send generic completion message on error
                await self.queue_directive(visitor, self.action.completion_message)
        else:
            # No completion handler registered, send generic message
            await self.queue_directive(visitor, self.action.completion_message)

        # Clean up and remove the session (always, regardless of handler success/failure)
        from ..utils.session_utils import cleanup_session

        await cleanup_session(session, visitor, self.action.get_class_name())

    async def generate_cancelled_directive(
        self, session: InterviewSession, visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Calls registered cancellation handler and cleans up session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        # Explicitly add cancellation event BEFORE queue_directive and cleanup
        # This ensures the event is recorded even if the session is removed
        # Mark event as added to prevent queue_directive from adding it again
        cancellation_event = self.action.get_state_event_message("CANCELLED")
        await visitor.add_event(cancellation_event)
        self._task_added = True  # Prevent duplicate task addition in queue_directive

        # Update task to cancelled (preserves task for audit log)
        action_name = self.action.get_class_name()
        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(action_name=action_name)
        await visitor.update_task(
            status="cancelled",
            description=description,
            action_name=action_name,
        )

        # Get cancellation handler for this interview type
        interview_type = session.interview_type
        cancelled_handler = self.action.get_cancelled_handler(interview_type)

        if cancelled_handler:
            try:
                await invoke_async_with_optional_context(
                    cancelled_handler,
                    session,
                    visitor=visitor,
                    interview_action=self.action,
                )
                # Cancellation handler is responsible for sending its own message if needed
            except Exception as e:
                logger.error(
                    f"{self.action.get_class_name()}: Cancellation handler failed: {e}",
                    exc_info=True,
                )
                # Send generic cancellation message on error
                await self.queue_directive(visitor, self.action.cancellation_message)
        else:
            # No cancellation handler registered, send generic message
            await self.queue_directive(visitor, self.action.cancellation_message)

        # Clean up and remove the session (always, regardless of handler success/failure)
        from ..utils.session_utils import cleanup_session

        await cleanup_session(session, visitor, self.action.get_class_name())
