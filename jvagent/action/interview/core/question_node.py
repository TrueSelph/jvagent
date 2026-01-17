"""QuestionNode for interview question handling.

This module provides QuestionNode, a node that represents individual interview questions
in the interview process with validation capabilities.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from .question_branch_evaluator import QuestionBranchEvaluator
from .enums import ValidationStatus

if TYPE_CHECKING:
    from .interview_session import InterviewSession

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
    
    state: Dict[str, Any] = attribute(
        default={},
        description="Question configuration containing 'name', 'question', 'constraints', and 'required'",
    )
    
    label: str = attribute(
        default_factory=str,
        description="Label for the node (typically the question name)",
    )

    async def on_register(self) -> None:
        """Register the node."""
    
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
            logger.warning(f"QuestionNode: Invalid callable reference type: {type(callable_ref).__name__}. Expected callable or string.")
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
                exc_info=True
            )
            return None
    
    async def process_input(
        self,
        raw_input: str,
        session: "InterviewSession",
        interaction: Optional[Any] = None
    ) -> Any:
        """Process raw user input before validation.
        
        This allows custom handlers to transform or normalize input before
        validation occurs. For example, converting "next Tuesday" to a date.
        
        Args:
            raw_input: Raw user input string
            session: Interview session for context
            interaction: Interaction node (optional, for accessing interaction context)
            
        Returns:
            Processed value ready for validation
        """
        constraints = self.state.get("constraints", {})
        question_name = self.state.get("name", "")
        
        # First, try to get handler from decorator registry (if action class is available)
        handler = None
        if question_name and session:
            action_class = self._get_action_class_from_session(session)
            if action_class:
                handler = action_class.get_input_handler(question_name)
        
        # Fallback to question_index string reference
        if not handler:
            input_handler_ref = constraints.get("input_handler")
            if input_handler_ref:
                handler = self._resolve_callable(input_handler_ref)
        
        # Execute handler if found
        if handler and callable(handler):
            try:
                # Handler must accept (raw_input, session, interaction)
                # Handler can be sync or async - check and await if needed
                import inspect
                if inspect.iscoroutinefunction(handler):
                    processed = await handler(raw_input, session, interaction)
                else:
                    processed = handler(raw_input, session, interaction)
                return processed
            except Exception as e:
                logger.warning(f"Input handler raised exception: {e}")
                return raw_input  # Fallback to raw input
        
        # Default: return input as-is
        return raw_input
    
    def _get_action_class_from_session(self, session: "InterviewSession") -> Optional[Any]:
        """Get the InterviewInteractAction class from session's interview_type.
        
        Args:
            session: Interview session with interview_type
            
        Returns:
            Action class if found, None otherwise
        """
        if not hasattr(session, 'interview_type') or not session.interview_type:
            return None
        
        interview_type = session.interview_type
        
        try:
            # Import the action module and get the class
            # The interview_type is the class name (e.g., "SignupInterviewInteractAction")
            # We need to find the module that contains this class
            import sys
            import inspect
            
            # Search through loaded modules for the class
            for module_name, module in sys.modules.items():
                if module is None:
                    continue
                try:
                    if hasattr(module, interview_type):
                        cls = getattr(module, interview_type)
                        # Check if it's a subclass of InterviewInteractAction
                        from jvagent.action.interview.interview_interact_action import InterviewInteractAction
                        if inspect.isclass(cls) and issubclass(cls, InterviewInteractAction):
                            return cls
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Exception while searching for action class '{interview_type}': {e}", exc_info=True)
            pass
        return None
    
    def condition_matches(
        self,
        condition: Dict[str, Any],
        session: "InterviewSession"
    ) -> bool:
        """Check if an edge condition matches the current session state.
        
        Args:
            condition: Condition dict with 'op' and optional 'value' keys (question is implicit)
            session: Interview session
            
        Returns:
            True if condition matches, False otherwise
        """
        # Question is implicit - use the question node's label as the implicit question
        question_name = self.state.get("name", "")
        if not question_name:
            return False
        return QuestionBranchEvaluator.matches(condition, session, implicit_question=question_name)

    async def execute(self, walker: Any) -> Optional[str]:
        """Execute question node to check if info is needed and return directive.

        Args:
            walker: Walker-like object with interview_session attribute

        Returns:
            Directive string if information is needed, None otherwise
        """
        logger.debug(f"QuestionNode executed for {self.label}")

        if not self.state.get("name", ""):
            return None

        # Check if this question has been answered
        question_key = self.state.get("name", "")
        session = getattr(walker, 'interview_session', None)
        
        if session and question_key in session.get_answered_questions():
            return None

        constraints = self.state.get("constraints", {})
        question = self.state.get("question", "")
        description = constraints.get("description", "")
        instructions = constraints.get("instructions", "")

        # Get template from walker (supplied by InterviewInteractAction)
        directive_template = getattr(walker, 'question_directive_template', None)
        
        # Return None if template not provided
        if not directive_template:
            return None

        # Format instructions - only include if present
        formatted_instructions = ""
        if instructions:
            formatted_instructions = f"\n\nNote: {instructions}"

        # Format directive with optional instructions
        directive = directive_template.format(
            question=question,
            description=description,
            instructions=formatted_instructions,
        )
        
        return directive if directive else None

    async def validate_response(
        self, 
        value: Any, 
        session: "InterviewSession"
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
            
        Returns:
            Tuple of (ValidationStatus, optional feedback message, optional corrected value)
            If corrected_value is provided, it should be used instead of the original value
        """
        # Process input first if it's a string (raw input)
        # Note: process_input should be idempotent
        # We pass None for interaction here since we don't have it in validate_response signature
        if isinstance(value, str):
            value = await self.process_input(value, session, interaction=None)
        
        constraints = self.state.get("constraints", {})
        question_key = self.state.get("name", "")
        
        # Check if value is empty/None
        if value is None or (isinstance(value, str) and not value.strip()):
            if self.state.get("required", False):
                return ValidationStatus.INVALID, "This field is required.", None
            return ValidationStatus.VALID, None, None  # Optional field can be empty
        
        # Type validation
        expected_type = constraints.get("type", "string")
        if expected_type == "string" and not isinstance(value, str):
            return ValidationStatus.INVALID, f"Expected a string value, got {type(value).__name__}", None
        elif expected_type == "number" or expected_type == "integer":
            try:
                float(value) if expected_type == "number" else int(value)
            except (ValueError, TypeError):
                return ValidationStatus.INVALID, f"Expected a {expected_type} value", None
        
        # Pattern/regex validation
        pattern = constraints.get("pattern")
        if pattern and isinstance(value, str):
            if not re.match(pattern, value):
                return ValidationStatus.INVALID, constraints.get("pattern_error", "Value doesn't match required format"), None
        
        # Email validation
        if constraints.get("format") == "email" and isinstance(value, str):
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, value):
                return ValidationStatus.INVALID, "Please provide a valid email address", None
        
        # Custom validation function (if provided)
        # First, try to get validator from decorator registry (if action class is available)
        validator = None
        question_name = self.state.get("name", "")
        
        if question_name and session:
            action_class = self._get_action_class_from_session(session)
            if action_class:
                validator = action_class.get_input_validator(question_name)
        
        # Fallback to question_index string reference
        if not validator:
            validator_ref = constraints.get("input_validator")
            if validator_ref:
                validator = self._resolve_callable(validator_ref)
        
        if validator and callable(validator):
            try:
                result = validator(value, session)
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
                        logger.warning(f"Validator '{validator.__name__}' returned unexpected tuple length: {len(result)}")
                        return ValidationStatus.INVALID, "Invalid validator return format", None
                elif isinstance(result, bool):
                    final_status = ValidationStatus.VALID if result else ValidationStatus.INVALID
                    return final_status, None, None
            except Exception as e:
                logger.error(f"Validator function '{validator.__name__}' raised exception: {e}", exc_info=True)
                return ValidationStatus.INVALID, f"Validation error: {str(e)}", None
        
        # Check for ambiguous values that might need clarification
        # This is a heuristic - can be customized per question via ambiguous_patterns in constraints
        # Returns VALID with optional feedback message (not a separate status)
        ambiguous_patterns = constraints.get("ambiguous_patterns", [])
        if isinstance(value, str):
            value_lower = value.lower()
            for pattern in ambiguous_patterns:
                if pattern in value_lower:
                    feedback = constraints.get("ambiguous_feedback", "I'd like to clarify this.")
                    return ValidationStatus.VALID, feedback, None
        
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
                logger.warning(f"Invalid validation status: {status}. Defaulting to INVALID.")
                return ValidationStatus.INVALID
        
        # Unknown type, default to INVALID
        logger.warning(f"Invalid validation status type: {type(status).__name__}. Defaulting to INVALID.")
        return ValidationStatus.INVALID

