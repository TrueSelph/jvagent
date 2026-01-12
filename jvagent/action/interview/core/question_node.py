"""QuestionNode for interview question handling.

This module provides QuestionNode, a node that represents individual interview questions
in the interview process with validation capabilities.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from .validation import ValidationStatus

if TYPE_CHECKING:
    from .interview_session import InterviewSession

logger = logging.getLogger(__name__)


class QuestionNode(Node):
    """Node representing an individual interview question.
    
    Each QuestionNode represents a single question in the interview flow with:
    - Question text and constraints (stored in state)
    - Three-tier validation (VALID, VALID_WITH_FLAG, INVALID)
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
        
        Supports multiple string reference formats:
        1. Full qualified path: "package.module.function_name"
        2. Module-qualified: "module_name.function_name" (tries to import module)
        3. Function name only: "function_name" (searches in sys.modules)
        
        Args:
            callable_ref: Either a callable object or a string reference
            
        Returns:
            Resolved callable object, or None if resolution fails
        """
        if callable(callable_ref):
            return callable_ref
        
        if not isinstance(callable_ref, str):
            return None
        
        # Try multiple resolution strategies
        strategies = [
            # Strategy 1: Full qualified path (e.g., "package.module.function")
            lambda ref: self._resolve_qualified_path(ref),
            # Strategy 2: Module.function format (e.g., "module_name.function_name")
            lambda ref: self._resolve_module_function(ref),
            # Strategy 3: Function name only - search in loaded modules
            lambda ref: self._resolve_function_name(ref),
        ]
        
        for strategy in strategies:
            try:
                result = strategy(callable_ref)
                if result is not None:
                    return result
            except Exception as e:
                continue
        
        logger.warning(f"QuestionNode: Failed to resolve callable '{callable_ref}' using any strategy")
        return None
    
    def _resolve_qualified_path(self, ref: str) -> Optional[Any]:
        """Resolve using full qualified path (package.module.function)."""
        parts = ref.rsplit(".", 1)
        if len(parts) == 2:
            module_name, func_name = parts
            # Try importing the module
            module = __import__(module_name, fromlist=[func_name])
            return getattr(module, func_name, None)
        return None
    
    def _resolve_module_function(self, ref: str) -> Optional[Any]:
        """Resolve using module.function format, checking sys.modules first."""
        import sys
        parts = ref.rsplit(".", 1)
        if len(parts) == 2:
            module_name, func_name = parts
            # Check if module is already loaded
            if module_name in sys.modules:
                module = sys.modules[module_name]
                return getattr(module, func_name, None)
            # Try importing
            try:
                module = __import__(module_name, fromlist=[func_name])
                return getattr(module, func_name, None)
            except ImportError:
                return None
        return None
    
    def _resolve_function_name(self, ref: str) -> Optional[Any]:
        """Resolve function name by searching in loaded modules."""
        import sys
        func_name = ref
        
        # Search through loaded modules for the function
        for module_name, module in sys.modules.items():
            if module is None:
                continue
            try:
                if hasattr(module, func_name):
                    func = getattr(module, func_name)
                    if callable(func) and not isinstance(func, type):
                        return func
            except Exception:
                continue
        
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
            condition: Condition dict with 'question' and 'equals' keys
            session: Interview session
            
        Returns:
            True if condition matches, False otherwise
        """
        if not condition:
            return False
        
        question_name = condition.get("question")
        expected_value = condition.get("equals")
        
        if not question_name or expected_value is None:
            return False
        
        actual_value = session.responses.get(question_name)
        return actual_value == expected_value

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
        - VALID: Response meets all constraints, store and continue
        - VALID_WITH_FLAG: Response is acceptable but needs clarification (e.g., "next Tuesday")
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
                        final_status = ValidationStatus(status) if isinstance(status, str) else status
                        return final_status, message, None
                    elif len(result) == 3:
                        # (status, message, corrected_value) - with correction
                        status, message, corrected_value = result
                        final_status = ValidationStatus(status) if isinstance(status, str) else status
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
        ambiguous_patterns = constraints.get("ambiguous_patterns", [])
        if isinstance(value, str):
            value_lower = value.lower()
            for pattern in ambiguous_patterns:
                if pattern in value_lower:
                    feedback = constraints.get("ambiguous_feedback", "I'd like to clarify this.")
                    return ValidationStatus.VALID_WITH_FLAG, feedback, None
        
        # All checks passed
        return ValidationStatus.VALID, None, None

