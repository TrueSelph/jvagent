"""QuestionNode for interview question handling.

This module provides QuestionNode, a node that represents individual interview questions
in the interview process with validation capabilities.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from ..foundation.decorators import get_input_context_provider
from ..foundation.enums import Intent, ValidationStatus
from ..foundation.exceptions import QuestionNotFoundError, ValidationError
from ..foundation.standard_validators import get_standard_validator
from ..utils.handler_utils import (
    invoke_async_with_optional_context,
    invoke_with_optional_context,
)
from .question_branch_evaluator import QuestionBranchEvaluator

if TYPE_CHECKING:
    from ..session.interview_session import InterviewSession

logger = logging.getLogger(__name__)


class QuestionNode(Node):
    """Node representing an individual interview question.

    Each QuestionNode represents a single question in the interview flow with:
    - Question text and constraints (stored in state)
    - Two-tier validation (VALID, INVALID) with optional feedback messages
    - Required vs optional flags
    - Validation rules embedded in constraints
    - Input handlers and validators coordination

    Note: Directive templates are managed by InterviewInteractAction and retrieved
    dynamically via the session's interview_type. QuestionNode focuses on question
    specifics, handlers, and validators only.
    """

    description: str = "Interview question node for gathering user information"

    agent_id: str = attribute(
        default=None, description="ID of the agent this question node belongs to"
    )

    interview_type: str = attribute(
        default=None,
        description="Type of interview this question belongs to (e.g., 'SignupInterviewInteractAction')",
    )

    state: Dict[str, Any] = attribute(
        default={},
        description="Question configuration containing 'name', 'question', 'constraints', and 'required'",
    )

    label: str = attribute(
        default_factory=str,
        description="Label for the node (typically the question name)",
    )

    _interview_action: Optional[Any] = (
        None  # Cached reference to the InterviewInteractAction class for handler
    )

    def _resolve_callable(self, callable_ref: Any) -> Optional[Any]:
        """Resolve a callable reference (function or string) to a callable object.

        Only supports fully qualified paths (package.module.function_name) for reliability.
        Validates the reference format early and provides clear error messages.

        Args:
            callable_ref: Either a callable object or a fully qualified string reference

        Returns:
            Resolved callable object, or None if resolution fails
        """
        if callable(callable_ref):
            return callable_ref

        if not isinstance(callable_ref, str):
            logger.warning(
                f"QuestionNode: Invalid callable reference type: {type(callable_ref).__name__}. Expected callable or string."
            )
            return None

        # Validate format: must be fully qualified path (at least module.function)
        if "." not in callable_ref:
            logger.error(
                f"QuestionNode: Invalid callable reference format: '{callable_ref}'. "
                f"Must be fully qualified path (e.g., 'package.module.function_name'). "
                f"Function name only is not supported to avoid conflicts."
            )
            return None

        # Resolve using fully qualified path only
        parts = callable_ref.rsplit(".", 1)
        if len(parts) != 2:
            logger.error(
                f"QuestionNode: Invalid callable reference format: '{callable_ref}'. "
                f"Expected format: 'package.module.function_name'"
            )
            return None

        module_name, func_name = parts

        try:
            # Import the module
            module = __import__(module_name, fromlist=[func_name])
            func = getattr(module, func_name, None)

            if func is None:
                logger.error(
                    f"QuestionNode: Function '{func_name}' not found in module '{module_name}'. "
                    f"Check that the function exists and is exported from the module."
                )
                return None

            if not callable(func):
                logger.error(
                    f"QuestionNode: '{callable_ref}' is not callable. "
                    f"Found {type(func).__name__} instead of a function."
                )
                return None

            return func

        except ImportError as e:
            logger.error(
                f"QuestionNode: Failed to import module '{module_name}' for callable '{callable_ref}': {e}. "
                f"Ensure the module path is correct and the module is importable."
            )
            return None
        except Exception as e:
            logger.error(
                f"QuestionNode: Unexpected error resolving callable '{callable_ref}': {e}",
                exc_info=True,
            )
            return None

    async def process_input(
        self,
        raw_input: str,
        session: "InterviewSession",
        interaction: Optional[Any] = None,
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
    ) -> Any:
        """Process raw user input before validation.

        This allows custom handlers to transform or normalize input before
        validation occurs. For example, converting "next Tuesday" to a date.

        Handlers may accept (raw_input, session, interaction) or the extended
        signature (raw_input, session, interaction, visitor, interview_action).
        Optional context is passed only when the handler accepts it.

        Args:
            raw_input: Raw user input string
            session: Interview session for context
            interaction: Interaction node (optional, for accessing interaction context)
            visitor: Optional InteractWalker for handler context
            interview_action: Optional InterviewInteractAction for handler context

        Returns:
            Processed value ready for validation
        """
        constraints = self.state.get("constraints", {})
        question_name = self.state.get("name", "")

        # First, try to get handler from decorator registry (if action class is available)
        handler = None
        if question_name and session:
            action = self._interview_action or interview_action
            if action:
                handler = action.get_input_handler(question_name)

        # Fallback to question config string reference
        if not handler:
            input_handler_ref = constraints.get("input_handler")
            if input_handler_ref:
                handler = self._resolve_callable(input_handler_ref)

        # Execute handler if found
        if handler and callable(handler):
            try:
                processed = await invoke_async_with_optional_context(
                    handler,
                    raw_input,
                    session,
                    interaction,
                    visitor=visitor,
                    interview_action=interview_action or self._interview_action,
                )
                return processed
            except Exception as e:
                logger.warning(f"Input handler raised exception: {e}")
                return raw_input  # Fallback to raw input

        # Default: return input as-is
        return raw_input

    async def get_context_data(
        self,
        session: "InterviewSession",
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Get context data for this question (static or dynamic).

        Context data provides additional information to help the user answer the question,
        such as available times, valid options, or personalized choices.

        Args:
            session: Interview session for context
            visitor: Optional InteractWalker for accessing graph context
            interview_action: Optional InterviewInteractAction for provider context

        Returns:
            Dictionary of context data to be included in the question prompt
        """
        # Start with static input_context from question config
        static_context = self.state.get("input_context", {})

        # Check for dynamic input_context_provider
        provider_name = self.state.get("input_context_provider")
        if provider_name:
            try:
                dynamic_context = await self._execute_context_provider(
                    provider_name,
                    session,
                    visitor,
                    interview_action=interview_action or self._interview_action,
                )
                # Merge static and dynamic (dynamic takes precedence)
                if dynamic_context:
                    return {**static_context, **dynamic_context}
            except Exception as e:
                logger.error(
                    f"Error executing input data provider '{provider_name}' for question '{self.state.get('name', '')}': {e}",
                    exc_info=True,
                )
                # Fall back to static context on error

        return static_context

    async def _execute_context_provider(
        self,
        provider_name: str,
        session: "InterviewSession",
        visitor: Optional[Any],
        interview_action: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute an input data provider function.

        Providers may accept (session, visitor) or (session, visitor, interview_action).
        Optional context is passed only when the provider accepts it.

        Args:
            provider_name: Name of the registered input data provider function
            session: Interview session
            visitor: Optional InteractWalker
            interview_action: Optional InterviewInteractAction for provider context

        Returns:
            Dictionary of context data from the provider function
        """
        # Look up function from registry
        func = get_input_context_provider(session.interview_type, provider_name)
        if not func:
            logger.error(
                f"Input data provider '{provider_name}' not found for interview type '{session.interview_type}'. "
                f"Question: '{self.state.get('name', '')}'"
            )
            return {}

        try:
            result = await invoke_async_with_optional_context(
                func,
                session=session,
                visitor=visitor,
                interview_action=interview_action,
            )

            # Validate result is a dictionary
            if not isinstance(result, dict):
                logger.warning(
                    f"Input data provider '{provider_name}' returned {type(result).__name__} "
                    f"but dict expected. Question: '{self.state.get('name', '')}'"
                )
                return {}

            logger.debug(
                f"Input data provider '{provider_name}' returned {len(result)} keys "
                f"for question '{self.state.get('name', '')}'"
            )
            return result

        except Exception as e:
            logger.error(
                f"Error executing input data provider '{provider_name}' for question '{self.state.get('name', '')}': {e}",
                exc_info=True,
            )
            return {}

    def _format_context_data(self, context_data: Dict[str, Any]) -> str:
        """Format context data for inclusion in the question directive.

        Args:
            context_data: Dictionary of context data

        Returns:
            Formatted string to include in the directive
        """
        if not context_data:
            return ""

        lines = []
        for key, value in context_data.items():
            # Format key as human-readable label
            label = key.replace("_", " ").title()

            # Format value based on type
            if isinstance(value, list):
                # Format lists with bullet points
                if value:
                    items = "\n  ".join(f"- {item}" for item in value)
                    lines.append(f"{label}:\n  {items}")
            elif isinstance(value, dict):
                # Format nested dicts (basic support)
                formatted_dict = ", ".join(f"{k}: {v}" for k, v in value.items())
                lines.append(f"{label}: {formatted_dict}")
            else:
                # Simple values
                lines.append(f"{label}: {value}")

        if lines:
            return "\n\nAvailable Context:\n" + "\n".join(lines)

        return ""

    async def execute(self, walker: Any) -> Optional[str]:
        """Execute question node to check if info is needed and return directive.

        Handles DECLINE intent:
        - REQUIRED field: returns REQUIRED_FIELD_DECLINE directive
        - OPTIONAL field: sets "N/A" response and returns None (walker continues)

        Args:
            walker: Walker-like object with interview_session attribute

        Returns:
            Directive string if information is needed, None otherwise
        """
        question_key = self.state.get("name", "")
        if not question_key:
            return None

        session = getattr(walker, "interview_session", None)
        if not session:
            return None

        # Already answered - nothing to do
        if question_key in session.get_answered_questions():
            return None

        self._interview_action = getattr(walker, "interview_action", None)
        current_intent = getattr(walker, "current_intent", None)
        is_required = self.state.get("required", False)

        # Handle DECLINE intent
        if current_intent == Intent.DECLINE:
            if is_required:
                # REQUIRED: return directive insisting on answer
                if self._interview_action:
                    decline_template = self._interview_action.required_field_decline
                    if decline_template:
                        field_display = question_key.replace("_", " ").title()
                        question = self.state.get("question", "")
                        return decline_template.format(
                            field_display=field_display,
                            question=question,
                        )
            else:
                # OPTIONAL: set N/A response and return None (let walker continue)
                session.set_response(question_key, "N/A")
                return None

        # Normal case - return question directive
        if not self._interview_action:
            return None

        directive_template = self._interview_action.question_directive
        if not directive_template:
            return None

        constraints = self.state.get("constraints", {})
        question = self.state.get("question", "")
        description = constraints.get("description", "")
        instructions = constraints.get("instructions", "")

        # Get context data for this question
        context_data = await self.get_context_data(
            session, walker, interview_action=self._interview_action
        )
        context_section = self._format_context_data(context_data)

        # Format instructions - only include if present
        formatted_instructions = ""
        if instructions:
            formatted_instructions = f"\n\nNote: {instructions}"

        # Format directive with optional context and instructions
        directive = directive_template.format(
            question=question,
            description=description,
            context_section=context_section,
            instructions=formatted_instructions,
        )

        return directive if directive else None

    def _validate_empty_value(
        self, value: Any
    ) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
        """Check if value is empty and validate based on required flag.

        Args:
            value: The value to check

        Returns:
            Validation result tuple if value is empty, None otherwise
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            if self.state.get("required", False):
                return ValidationStatus.INVALID, "This field is required.", None
            return ValidationStatus.VALID, None, None
        return None

    def _run_standard_validators(
        self, value: Any, constraints: Dict[str, Any]
    ) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
        """Run standard validators based on constraints.

        Checks constraints for:
        - 'type': string, number, integer (runs type validator)
        - 'format': email, phone, url (runs format validator)
        - 'pattern': regex pattern (runs pattern validator)
        - 'standard_validators': list of validator names to run

        Returns first INVALID result, or None if all pass.

        Args:
            value: The value to validate
            constraints: Question constraints dictionary

        Returns:
            Validation result tuple if any validator fails, None if all pass
        """
        # Type validation
        expected_type = constraints.get("type", "string")
        if expected_type in ("string", "number", "integer"):
            validator = get_standard_validator(expected_type)
            if validator:
                result = validator(value, constraints)
                if result and result[0] == ValidationStatus.INVALID:
                    return result

        # Pattern validation (if pattern is specified)
        if constraints.get("pattern"):
            validator = get_standard_validator("pattern")
            if validator:
                result = validator(value, constraints)
                if result and result[0] == ValidationStatus.INVALID:
                    return result

        # Format validation (email, phone, url, etc.)
        format_type = constraints.get("format")
        if format_type:
            validator = get_standard_validator(format_type)
            if validator:
                result = validator(value, constraints)
                if result and result[0] == ValidationStatus.INVALID:
                    return result

        # Additional standard validators from list
        standard_validators = constraints.get("standard_validators", [])
        if isinstance(standard_validators, list):
            for validator_name in standard_validators:
                validator = get_standard_validator(validator_name)
                if validator:
                    result = validator(value, constraints)
                    if result and result[0] == ValidationStatus.INVALID:
                        return result

        return None

    def _get_custom_validator(
        self,
        session: "InterviewSession",
        constraints: Dict[str, Any],
        interview_action: Optional[Any] = None,
    ) -> Optional[Callable]:
        """Get custom validator function from decorator registry or constraints.

        Args:
            session: Interview session
            constraints: Question constraints dictionary
            interview_action: Optional InterviewInteractAction (falls back to self._interview_action)

        Returns:
            Validator function if found, None otherwise
        """
        question_name = self.state.get("name", "")

        # Try decorator registry first
        if question_name and session:
            action = self._interview_action or interview_action
            if action:
                validator = action.get_input_validator(question_name)
                if validator:
                    return validator

        # Fallback to string reference in constraints
        validator_ref = constraints.get("input_validator")
        if validator_ref:
            return self._resolve_callable(validator_ref)

        return None

    async def _execute_custom_validator(
        self,
        validator: Callable,
        value: Any,
        session: "InterviewSession",
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str], Optional[Any]]:
        """Execute custom validator function and handle its result.

        Validators may accept (value, session) or (value, session, visitor, interview_action).
        Optional context is passed only when the validator accepts it.

        Args:
            validator: The validator function to execute
            value: The value to validate
            session: Interview session
            visitor: Optional InteractWalker for validator context
            interview_action: Optional InterviewInteractAction for validator context

        Returns:
            Validation result tuple
        """
        question_name = self.state.get("name", "")

        try:
            result = await invoke_async_with_optional_context(
                validator,
                value,
                session,
                visitor=visitor,
                interview_action=interview_action or self._interview_action,
            )
            if isinstance(result, tuple):
                # Handle different tuple lengths
                if len(result) == 2:
                    # (status, message) - no correction
                    status, message = result
                    final_status = self._normalize_validation_status(status)
                    return final_status, message, None
                elif len(result) == 3:
                    # (status, message, corrected_value) - with correction
                    status, message, corrected_value = result
                    final_status = self._normalize_validation_status(status)
                    return final_status, message, corrected_value
                else:
                    logger.warning(
                        f"Validator '{validator.__name__}' returned unexpected tuple length: {len(result)}"
                    )
                    return (
                        ValidationStatus.INVALID,
                        "Invalid validator return format",
                        None,
                    )
            elif isinstance(result, bool):
                final_status = (
                    ValidationStatus.VALID if result else ValidationStatus.INVALID
                )
                return final_status, None, None
        except ValidationError as e:
            logger.debug(f"Validator raised ValidationError: {e}")
            return ValidationStatus.INVALID, e.message, None
        except Exception as e:
            logger.error(
                f"Validator function '{validator.__name__}' raised exception: {e}",
                exc_info=True,
            )
            validation_error = ValidationError(
                question_name or "unknown", f"Validation error: {str(e)}", value
            )
            return ValidationStatus.INVALID, validation_error.message, None

        # If we get here, validator returned unexpected type
        return (
            ValidationStatus.INVALID,
            "Validator returned unexpected result type",
            None,
        )

    def _check_ambiguous_patterns(
        self, value: Any, constraints: Dict[str, Any]
    ) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
        """Check for ambiguous patterns that might need clarification.

        Args:
            value: The value to check
            constraints: Question constraints dictionary

        Returns:
            Validation result with feedback if ambiguous pattern found, None otherwise
        """
        ambiguous_patterns = constraints.get("ambiguous_patterns", [])
        if isinstance(value, str) and ambiguous_patterns:
            value_lower = value.lower()
            for pattern in ambiguous_patterns:
                if pattern in value_lower:
                    feedback = constraints.get(
                        "ambiguous_feedback", "I'd like to clarify this."
                    )
                    return ValidationStatus.VALID, feedback, None
        return None

    async def validate_response(
        self,
        value: Any,
        session: "InterviewSession",
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str], Optional[Any]]:
        """Validate a response value against this question's constraints.

        Enhanced to call process_input() first if value is a string.

        Returns (validation_status, feedback_message, corrected_value)

        Status can be:
        - VALID: Response meets all constraints, store and continue. May include optional feedback message for clarification.
        - INVALID: Response doesn't meet constraints, needs correction

        Args:
            value: The extracted response value (may be raw string)
            session: The interview session for context
            visitor: Optional InteractWalker for validator context
            interview_action: Optional InterviewInteractAction for validator context

        Returns:
            Tuple of (ValidationStatus, optional feedback message, optional corrected value)
            If corrected_value is provided, it should be used instead of the original value

        Note:
            Caller (InterviewWalker) is responsible for calling process_input before
            validate_response. Do not call process_input here to avoid double execution.
        """
        constraints = self.state.get("constraints", {})

        # Check if value is empty
        empty_result = self._validate_empty_value(value)
        if empty_result:
            return empty_result

        # Run standard validators (type, format, pattern, etc.)
        standard_result = self._run_standard_validators(value, constraints)
        if standard_result and standard_result[0] == ValidationStatus.INVALID:
            return standard_result

        # Custom validation function (runs AFTER standard validators)
        validator = self._get_custom_validator(
            session, constraints, interview_action=interview_action
        )
        if validator and callable(validator):
            return await self._execute_custom_validator(
                validator,
                value,
                session,
                visitor=visitor,
                interview_action=interview_action or self._interview_action,
            )

        # Check for ambiguous patterns
        ambiguous_result = self._check_ambiguous_patterns(value, constraints)
        if ambiguous_result:
            return ambiguous_result

        # All checks passed
        return ValidationStatus.VALID, None, None

    def _normalize_validation_status(self, status: Any) -> ValidationStatus:
        """Normalize validation status.

        Args:
            status: Validation status (string, enum, or ValidationStatus)

        Returns:
            Normalized ValidationStatus
        """
        if isinstance(status, ValidationStatus):
            return status

        if isinstance(status, str):
            try:
                return ValidationStatus(status)
            except ValueError:
                logger.warning(
                    f"Invalid validation status: {status}. Defaulting to INVALID."
                )
                return ValidationStatus.INVALID

        # Unknown type, default to INVALID
        logger.warning(
            f"Invalid validation status type: {type(status).__name__}. Defaulting to INVALID."
        )
        return ValidationStatus.INVALID
