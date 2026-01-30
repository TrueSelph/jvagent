"""Response processing for interview action.

This module handles processing, validation, and storage of user responses.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..session.interview_session import InterviewSession
from ..graph.question_node import QuestionNode
from ..graph.question_walker import QuestionWalker
from ..foundation.enums import ValidationStatus, ContextKey, InterviewState
from ..utils.session_utils import sort_fields_by_question_order
from ..foundation.exceptions import QuestionNotFoundError

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class ResponseProcessor:
    """Handles processing and validation of user responses."""

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize response processor with action instance.

        Args:
            action: InterviewInteractAction instance
        """
        self.action = action

    async def handle_update_inline(
        self,
        classification_result: "ClassificationResult",
        session: InterviewSession,
        visitor: "InteractWalker",
        interaction: "Interaction"
    ) -> bool:
        """Handle update inline using QuestionNode processing/validation.

        Reuses the same QuestionNode flow as SUBMISSION for consistency.

        Args:
            classification_result: Classification result with UPDATE intent
            session: Interview session
            visitor: InteractWalker
            interaction: Current interaction

        Returns:
            True if update completed, False if prompting for value or validation failed
        """
        field = classification_result.field
        # Normalize field - handle string "null" or empty string
        if field and isinstance(field, str):
            field = field.strip()
            if field.lower() in ("null", "none", ""):
                field = None
                classification_result.field = None

        if not field:
            logger.warning(f"{self.action.get_class_name()}: Update field is None")
            return False

        # Get question node
        try:
            question_node = await self.action._get_question_node(field, session)
            if not question_node:
                raise QuestionNotFoundError(field)
        except QuestionNotFoundError as e:
            # Provide user-friendly message
            field_display = field.replace("_", " ").title()
            await self.action.directive_builder.queue_directive(
                visitor,
                f"Tell the user: I encountered an issue processing the {field_display} field. "
                f"Please try again or contact support if the problem persists."
            )
            logger.error(f"{self.action.get_class_name()}: {e}", exc_info=True)
            return False

        # If value is missing, prompt for it
        if classification_result.value is None:
            current_value = session.get_response(field)
            field_display = field.replace("_", " ").title()

            directive = self.action.update_prompt_for_value_template.format(
                field_display=field_display,
                current_value=current_value
            )

            await self.action._queue_directive(visitor, directive)
            return False  # Update not completed, waiting for value

        # Process and validate the new value (same flow as SUBMISSION)
        new_value = classification_result.value

        # Process input first (handles custom transformations like autocorrection)
        if isinstance(new_value, str):
            new_value = await question_node.process_input(new_value, session, interaction)

        # Validate the processed value
        validation_status, feedback, corrected_value = await question_node.validate_response(new_value, session)

        # Use corrected value if validator provided one
        if corrected_value is not None:
            new_value = corrected_value
        session.set_validation_status(field, validation_status)

        if validation_status == ValidationStatus.VALID:
            # Update session with audit trail
            old_value = session.get_response(field)
            session.update_response(field, new_value, old_value)
            await session.save()
            logger.debug(f"{self.action.get_class_name()}: Updated {field} to {new_value}")

            # Re-evaluate conditional graph after update (may skip subsequent questions or trigger state transitions)
            from ..graph.question_walker import QuestionWalker
            question_walker = QuestionWalker()
            question_walker.interview_session = session
            question_walker.interaction = interaction
            await self.action._update_reachable_questions(session, question_walker, just_answered_field=field)

            # Check for directive override after successful update
            override_func = self.action.get_input_directive_override(field)
            if override_func:
                try:
                    override_result = await self.action._call_override_function(
                        override_func, field, new_value, session, interaction, visitor
                    )

                    # Only process override if it's not None (None means use default)
                    if override_result is not None:
                        # Inspect override_result to determine mode BEFORE processing
                        override_mode = None
                        if isinstance(override_result, tuple) and len(override_result) == 2:
                            override_mode = override_result[0].lower()  # "append" or "replace"

                        # Get default confirmation directive
                        field_display = field.replace("_", " ").title()
                        default_directive = f"Tell the user: Updated {field_display} to {new_value}."

                        # Process override result - returns (default_directive, custom_directive)
                        default_to_queue, custom_to_queue = self.action._process_directive_override(override_result, default_directive)

                        # Handle based on explicit mode (not based on which directives are present)
                        if override_mode == "replace":
                            # Replace mode: only custom directive, no default
                            if custom_to_queue:
                                await self.action._queue_directive(visitor, custom_to_queue)
                            # Set flag to prevent normal flow from continuing
                            if session.context is None:
                                session.context = {}
                            session.context[ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE] = True
                            await session.save()
                            return True  # Update completed with replace mode override
                        elif override_mode == "append":
                            # Append mode: queue both default and custom
                            if default_to_queue:
                                await self.action._queue_directive(visitor, default_to_queue)
                            if custom_to_queue:
                                await self.action._queue_directive(visitor, custom_to_queue)
                            # Return True if directives were queued
                            if default_to_queue or custom_to_queue:
                                return True  # Update completed with append mode override
                        elif custom_to_queue:
                            # Simple string override: queue custom directive
                            await self.action._queue_directive(visitor, custom_to_queue)
                            return True  # Update completed with custom directive
                        elif default_to_queue:
                            # Simple string override: queue default
                            await self.action._queue_directive(visitor, default_to_queue)
                            return True  # Update completed with default directive
                except Exception as e:
                    logger.warning(f"{self.action.get_class_name()}: Directive override raised exception: {e}", exc_info=True)

            # If feedback is provided with VALID status, send it as clarification
            if feedback:
                # Ensure proper prefix if not already present
                feedback_msg = feedback
                if not feedback_msg.startswith("Tell the user:") and not feedback_msg.startswith("Ask:"):
                    feedback_msg = f"Ask: {feedback_msg}"
                await self.action._queue_directive(visitor, feedback_msg)

            return True  # Update completed

        else:  # INVALID
            # Don't store, ask for correction
            error_msg = feedback or f"Tell the user: Please provide a valid value for {field}."
            if not error_msg.startswith("Tell the user:") and not error_msg.startswith("Ask:"):
                error_msg = f"Tell the user: {error_msg}"
            await self.action._queue_directive(visitor, error_msg)
            return False  # Update failed, waiting for valid value

    async def process_responses_to_questions(
        self,
        responses: Dict[str, Any],
        session: InterviewSession,
        visitor: "InteractWalker",
        interaction: "Interaction",
        question_walker: QuestionWalker
    ) -> None:
        """Process and validate extracted responses using QuestionWalker.

        Processes all extracted responses in question_index order, storing valid ones
        and tracking the first invalid field. Respects conditional edges between questions.

        Args:
            responses: Extracted responses dictionary
            session: Interview session
            visitor: InteractWalker
            interaction: Current interaction
            question_walker: QuestionWalker instance
        """
        # Track results
        valid_fields = []
        first_invalid_feedback = None
        replace_mode_used = False  # Track if any override used replace mode
        append_mode_overrides = []  # Track fields with append mode overrides

        # Sort responses by question_index order for sequential processing
        sorted_fields = sort_fields_by_question_order(list(responses.keys()), session)

        for field in sorted_fields:
            value = responses[field]

            # Check if this question is reachable given current state and conditional edges
            if not await question_walker.should_process_question(field, session):
                logger.debug(f"{self.action.get_class_name()}: Skipping {field} - not reachable given current conditional edges")
                continue

            # Find question node for validation
            try:
                question_node = await self.action._get_question_node(field, session)
                if not question_node:
                    raise QuestionNotFoundError(field)
            except QuestionNotFoundError as e:
                # Skip this field and continue with others
                logger.warning(
                    f"{self.action.get_class_name()}: Skipping field '{field}' - question node not found. "
                    f"This field will not be processed."
                )
                continue

            # Use QuestionWalker to process and validate
            # Returns (final_value, validation_status, feedback) where final_value may be autocorrected
            final_value, validation_status, feedback = await question_walker.process_and_validate(
                value,
                question_node,
                session,
                interaction
            )
            session.set_validation_status(field, validation_status)

            # Check if there's a custom validator registered for this field
            has_custom_validator = False
            question_name = question_node.state.get("name", "")
            if question_name and session:
                action_class = question_node._get_action_class_from_session(session)
                if action_class:
                    validator = action_class.get_input_validator(question_name)
                    if validator:
                        has_custom_validator = True

            # Also check for string reference validator
            constraints = question_node.state.get("constraints", {})
            if not has_custom_validator and constraints.get("input_validator"):
                has_custom_validator = True

            if validation_status == ValidationStatus.VALID:
                # Store final value (may be autocorrected) - only if valid
                session.set_response(field, final_value)
                valid_fields.append(field)

                # Re-evaluate conditional graph after each storage (may skip subsequent questions)
                # This will evaluate branches and trigger state transitions if needed
                state_before = session.state
                await self.action._update_reachable_questions(
                    session, question_walker, just_answered_field=field
                )
                state_after = session.state

                # If state transition occurred, return early to let generate_active_directive handle it
                if state_before != state_after:
                    session.active_question_key = None
                    await session.save()
                    logger.debug(
                        f"{self.action.get_class_name()}: State transition occurred "
                        f"during response processing for field '{field}': "
                        f"{state_before.value} -> {state_after.value}"
                    )
                    return

                # Save session if no state transition occurred
                await session.save()

                # Check for directive override after successful storage
                override_func = self.action.get_input_directive_override(field)
                if override_func:
                    try:
                        override_result = await self.action._call_override_function(
                            override_func, field, final_value, session, interaction, visitor
                        )

                        # Only process override if it's not None (None means use default)
                        if override_result is not None:
                            # Inspect override_result to determine mode BEFORE processing
                            override_mode = None
                            if isinstance(override_result, tuple) and len(override_result) == 2:
                                override_mode = override_result[0].lower()  # "append" or "replace"

                            # Use feedback as default directive if available (for simple string mode)
                            default_directive = feedback if feedback else ""

                            # Process override result - returns (default_directive, custom_directive)
                            default_to_queue, custom_to_queue = self.action._process_directive_override(
                                override_result, default_directive
                            )

                            # Handle based on explicit mode (not based on which directives are present)
                            if override_mode == "replace":
                                # Replace mode: only custom directive, no default
                                if custom_to_queue:
                                    await self.action._queue_directive(visitor, custom_to_queue)
                                replace_mode_used = True
                            elif override_mode == "append":
                                # Append mode: track for later processing (need next question directive)
                                if custom_to_queue:
                                    append_mode_overrides.append((field, custom_to_queue))
                            elif custom_to_queue:
                                # Simple string override: queue custom directive
                                await self.action._queue_directive(visitor, custom_to_queue)
                            elif default_to_queue:
                                # Simple string override: queue default now (feedback-based)
                                await self.action._queue_directive(visitor, default_to_queue)
                    except Exception as e:
                        logger.warning(f"{self.action.get_class_name()}: Directive override raised exception: {e}", exc_info=True)

                # If feedback is provided with VALID status, send it as clarification
                # This handles cases where the value is acceptable but needs clarification (e.g., "next Tuesday")
                # Continue processing other fields even if feedback is sent
                if feedback:
                    # Use feedback message as-is without prepending
                    feedback_msg = feedback
                    await self.action._queue_directive(visitor, feedback_msg)

            else:  # INVALID
                # Track first invalid field only
                if not first_invalid_feedback:
                    # Generate directive with validation feedback
                    if has_custom_validator and feedback:
                        # Use validator's feedback message as-is without prepending
                        error_msg = feedback
                    else:
                        # Fallback to generic message
                        error_msg = feedback or f"Tell the user: Please provide a valid value for {field}."
                    first_invalid_feedback = (field, error_msg)

        # Handle first invalid field if any
        if first_invalid_feedback:
            field, error_msg = first_invalid_feedback
            # Keep active_question_key pointing to this field so we can re-ask
            session.active_question_key = field
            await session.save()
            await self.action._queue_directive(visitor, error_msg)
            return  # Stop here, wait for correction

        # All processed successfully
        # Update active_question_key to last processed field
        if valid_fields:
            session.active_question_key = valid_fields[-1]
            await session.save()

        # Handle append mode overrides: get next question directive and queue it with custom directives
        if append_mode_overrides:
            # Use existing question_walker (already configured)
            next_question_node = await question_walker.find_next_question(session, self.action)

            # Note: State transitions should have already occurred during response processing
            # If state is not ACTIVE, let generate_active_directive handle it
            if session.state != InterviewState.ACTIVE:
                # State transition happened - let generate_active_directive handle it
                # Don't queue append mode directives, state directive takes precedence
                session.active_question_key = None
                await session.save()
                logger.debug(
                    f"{self.action.get_class_name()}: State is {session.state.value}, "
                    f"skipping append directives"
                )
                return

            # Queue next question directive if it exists
            if next_question_node:
                next_question_directive = await next_question_node.execute(question_walker) or ""
                if next_question_directive:
                    await self.action._queue_directive(visitor, next_question_directive)
                    # Set flag to prevent normal flow from queueing next question again
                    if session.context is None:
                        session.context = {}
                    session.context[ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE] = True
                    await session.save()

            # ALWAYS queue custom directives (even if no next question)
            for field, custom_directive in append_mode_overrides:
                await self.action._queue_directive(visitor, custom_directive)

        # If replace mode override was used, set flag to prevent normal flow from finding next question
        if replace_mode_used:
            if session.context is None:
                session.context = {}
            session.context[ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE] = True

        # Clear active_question_key
        session.active_question_key = None
        await session.save()
