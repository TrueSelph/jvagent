"""Directive builder for interview action.

Extracted directive formatting and generation logic from interview_interact_action.py
for better separation of concerns.
"""

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

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
        self._task_id: Optional[str] = None

    def reset_task_tracking(self) -> None:
        """Reset task tracking flag for new execution."""
        self._task_added = False
        self._task_id = None

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

    async def _start_active_task(
        self,
        visitor: "InteractWalker",
        *,
        description: str,
        owner_action: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Start (or adopt) the active interview task for this owner_action.

        Deduplication: if an existing active task with the same
        ``owner_action`` is already on the conversation, adopt it rather than
        creating a new one. This prevents the conversation task list from
        accumulating one entry per interaction touch (which is what produced
        the duplicate-task traces).

        When an existing active task is found, its ``data`` block is merged
        with the incoming ``data`` — newer values (current interview state,
        for instance) win — and the task is persisted. The handle's
        ``description`` / ``title`` are NOT overwritten so the original
        registration text stays stable across the flow.
        """
        existing = self._find_existing_active_task(visitor, owner_action)
        if existing is not None:
            # Refresh state metadata so the router sees the latest interview
            # state (active → review → ...). Other data fields are preserved.
            if isinstance(data, dict) and data:
                try:
                    await existing.update(**data)
                except Exception as exc:
                    logger.debug(
                        "DirectiveBuilder: failed to refresh task data: %s",
                        exc,
                    )
            self._task_id = existing.id
            return

        handle = await visitor.tasks.create(
            title=description,
            description=description,
            owner_action=owner_action,
            task_type="INTERVIEW",
            data=data,
        )
        await handle.start()
        self._task_id = handle.id

    @staticmethod
    def _find_existing_active_task(
        visitor: "InteractWalker", owner_action: str
    ) -> Optional[Any]:
        """Return the first active task matching ``owner_action``, or None."""
        try:
            store = visitor.tasks
        except Exception:
            return None
        try:
            existing = store.list(status="active", owner_action=owner_action)
        except Exception:
            return None
        return existing[0] if existing else None

    async def _update_task_status(
        self,
        visitor: "InteractWalker",
        *,
        status: str,
        description: str = "",
        action_name: str = "",
    ) -> None:
        """Update/complete interview task via conversation task store."""
        if not self._task_id:
            return
        handle = visitor.tasks.get(self._task_id)
        if not handle:
            return
        if status == "completed":
            await handle.complete()
        elif status == "cancelled":
            await handle.cancel()
        elif status == "failed":
            await handle.fail()

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
                        action_title = self.action.metadata.get("title", "")
                        action_title = action_title.split("Interact")[0].strip()
                        action_title = action_title.replace("Action", "").strip()
                        action_description = self.action.description
                        # description = f"The user was in the {action_title}. A guide asked if they wanted to continue the {action_title}. (Action Description: {action_description}). If the user message diverges from the active task, respond to it, but close by reminding them to return and complete the {action_title}."

                        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
                            action_title=action_title,
                            action_description=action_description,
                        )

                        data = {
                            "interview_type": session.interview_type,
                            "state": session.state.value,
                        }
                        await self._start_active_task(
                            visitor,
                            description=description,
                            owner_action=action_name,
                            data=data,
                        )
                else:
                    # No session available, default to active task
                    action_name = self.action.get_class_name()
                    action_title = self.action.metadata.get("title", "")
                    action_title = action_title.split("Interact")[0].strip()
                    action_title = action_title.replace("Action", "").strip()
                    action_description = self.action.description
                    # description = f"The user was in the {action_title}. A guide asked if they wanted to continue the {action_title}. (Action Description: {action_description}). If the user message diverges from the active task, respond to it, but close by reminding them to return and complete the {action_title}."

                    description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
                        action_title=action_title, action_description=action_description
                    )

                    await self._start_active_task(
                        visitor,
                        description=description,
                        owner_action=action_name,
                    )
                self._task_added = True

            await visitor.add_directive(directive)
        else:
            logger.warning(
                f"{self.action.get_class_name()}: Attempted to queue empty directive"
            )

    async def _emit_terminal_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker",
        event_type: str,
        task_status: str,
        handler: Optional[Callable[..., Any]],
        default_message: str,
    ) -> None:
        """Shared flow for COMPLETED and CANCELLED terminal directives.

        Adds event, updates task status, calls handler or queues default message,
        then cleans up session.
        """
        event = self.action.get_state_event_message(event_type)
        await visitor.add_event(event)
        self._task_added = True

        # action_name = self.action.get_class_name()
        # description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(action_name=action_name)
        action_name = self.action.get_class_name()
        action_title = self.action.metadata.get("title", "")
        action_title = action_title.split("Interact")[0].strip()
        action_title = action_title.replace("Action", "").strip()
        action_description = self.action.description
        # description = f"The user was in the {action_title}. A guide asked if they wanted to continue the {action_title}. (Action Description: {action_description}). If the user message diverges from the active task, respond to it, but close by reminding them to return and complete the {action_title}."

        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
            action_title=action_title, action_description=action_description
        )
        await self._update_task_status(
            visitor,
            status=task_status,
        )

        if handler:
            try:
                await invoke_async_with_optional_context(
                    handler,
                    session,
                    visitor=visitor,
                    interview_action=self.action,
                )
            except Exception as e:
                logger.error(
                    f"{action_name}: {event_type} handler failed: {e}",
                    exc_info=True,
                )
                await self.queue_directive(visitor, default_message)
        else:
            await self.queue_directive(visitor, default_message)

        from ..utils.session_utils import cleanup_session

        await cleanup_session(session, visitor, action_name)

    async def generate_completed_directive(
        self, session: InterviewSession, visitor: "InteractWalker"
    ) -> None:
        """Generate directive for COMPLETED state.

        Calls registered completion handler and cleans up session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        interview_type = session.interview_type
        handler = self.action.get_completion_handler(interview_type)
        await self._emit_terminal_directive(
            session,
            visitor,
            event_type="COMPLETED",
            task_status="completed",
            handler=handler,
            default_message=self.action.completion_message,
        )

    async def generate_cancelled_directive(
        self, session: InterviewSession, visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Calls registered cancellation handler and cleans up session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        interview_type = session.interview_type
        handler = self.action.get_cancelled_handler(interview_type)
        await self._emit_terminal_directive(
            session,
            visitor,
            event_type="CANCELLED",
            task_status="cancelled",
            handler=handler,
            default_message=self.action.cancellation_message,
        )
