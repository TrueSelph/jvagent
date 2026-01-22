"""Decorators for interview action extensions.

This module provides decorators for registering custom handlers, validators,
directive overrides, and completion handlers for interview actions.
"""

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

if TYPE_CHECKING:
    from jvagent.action.interview.core.session.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.base import InteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)

# Module-level registry for completion handlers (keyed by interview_type)
# This is populated when @on_interview_complete decorated functions are defined
_completion_handlers: Dict[str, Callable] = {}

# Module-level registries for decorator-registered handlers, validators, and directive overrides
# Format: {(interview_type, question_name): function}
_input_handler_registry: Dict[Tuple[str, str], Callable] = {}
_input_validator_registry: Dict[Tuple[str, str], Callable] = {}
_input_directive_override_registry: Dict[Tuple[str, str], Callable] = {}

# Pending registrations: functions decorated before their class is defined
# Format: {interview_type: {question_name: function}}
_pending_input_handlers: Dict[str, Dict[str, Callable]] = {}
_pending_input_validators: Dict[str, Dict[str, Callable]] = {}
_pending_input_directive_overrides: Dict[str, Dict[str, Callable]] = {}

# Module-level registry for branch functions
# Format: {(interview_type, function_name): function}
_branch_function_registry: Dict[Tuple[str, str], Callable] = {}
_pending_branch_functions: Dict[str, Dict[str, Callable]] = {}


def input_handler(question_name: str, interview_type: Optional[str] = None):
    """Decorator to register an input handler for a specific question.

    Input handlers process raw user input before validation (e.g., normalize time expressions).

    The decorator registers the handler in a module-level registry.
    The interview_type is determined from the module where the handler is defined
    by looking for InterviewInteractAction subclasses in that module.

    Args:
        question_name: Name of the question (must match 'name' field in question_graph)

    Example:
        @input_handler('available_times')
        async def normalize_time(raw_input: str, session: InterviewSession, interaction: Interaction) -> str:
            # Normalize time input
            return normalized_time
    """
    def decorator(func: Callable) -> Callable:
        # Store the question name on the function for later lookup
        func._interview_question_name = question_name  # type: ignore
        func._interview_handler_type = "input_handler"  # type: ignore

        # Try to determine the interview_type from the module where this function is defined
        # or use the provided interview_type parameter
        determined_type = interview_type
        try:
            if not determined_type:
                module = inspect.getmodule(func)
                if module:
                    # Import here to avoid circular dependency
                    from jvagent.action.interview.interview_interact_action import InterviewInteractAction
                    # Look for InterviewInteractAction subclasses in the module
                    for name, obj in vars(module).items():
                        if (inspect.isclass(obj) and
                            issubclass(obj, InterviewInteractAction) and
                            obj is not InterviewInteractAction):
                            determined_type = obj.__name__
                            break
            
            if determined_type:
                # Register in module-level registry
                _input_handler_registry[(determined_type, question_name)] = func
            else:
                # Store in pending registry if interview_type is provided but class not yet defined
                # Otherwise, rely on class attribute scanning in __init_subclass__
                if interview_type:
                    if interview_type not in _pending_input_handlers:
                        _pending_input_handlers[interview_type] = {}
                    _pending_input_handlers[interview_type][question_name] = func
        except Exception as e:
            logger.warning(f"Error registering handler '{func.__name__}': {e}")

        return func
    return decorator


def input_validator(question_name: str, interview_type: Optional[str] = None):
    """Decorator to register a validator for a specific question.

    Validators validate responses with custom logic.

    The decorator registers the validator in a module-level registry.
    The interview_type is determined from the module where the validator is defined
    by looking for InterviewInteractAction subclasses in that module.

    Args:
        question_name: Name of the question (must match 'name' field in question_graph)

    Example:
        @input_validator('user_email')
        def validate_email(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
            # Validate email
            return ValidationStatus.VALID, None
    """
    def decorator(func: Callable) -> Callable:
        # Store the question name on the function for later lookup
        func._interview_question_name = question_name  # type: ignore
        func._interview_handler_type = "input_validator"  # type: ignore

        # Try to determine the interview_type from the module where this function is defined
        # or use the provided interview_type parameter
        determined_type = interview_type
        try:
            if not determined_type:
                module = inspect.getmodule(func)
                if module:
                    # Import here to avoid circular dependency
                    from jvagent.action.interview.interview_interact_action import InterviewInteractAction
                    # Look for InterviewInteractAction subclasses in the module
                    for name, obj in vars(module).items():
                        if (inspect.isclass(obj) and
                            issubclass(obj, InterviewInteractAction) and
                            obj is not InterviewInteractAction):
                            determined_type = obj.__name__
                            break
            
            if determined_type:
                # Register in module-level registry
                _input_validator_registry[(determined_type, question_name)] = func
            else:
                # Store in pending registry if interview_type is provided but class not yet defined
                # Otherwise, rely on class attribute scanning in __init_subclass__
                if interview_type:
                    if interview_type not in _pending_input_validators:
                        _pending_input_validators[interview_type] = {}
                    _pending_input_validators[interview_type][question_name] = func
        except Exception as e:
            logger.warning(f"Error registering validator '{func.__name__}': {e}")

        return func
    return decorator


def input_directive_override(question_name: str, interview_type: Optional[str] = None):
    """Decorator to register a directive override for a specific question.

    Directive overrides allow customizing agent responses after a field value is
    successfully validated and stored. They can replace or append to the default directive.

    The decorator registers the override in a module-level registry.
    The interview_type is determined from the module where the override is defined
    by looking for InterviewInteractAction subclasses in that module.

    Args:
        question_name: Name of the question (must match 'name' field in question_graph)

    Handler Signature:
        The handler must accept five parameters:
        - field_name: str - Name of the field that was just stored
        - value: Any - The value that was stored
        - session: InterviewSession - Interview session for context
        - interaction: Interaction - Current interaction
        - visitor: InteractWalker - Walker for context

    Returns:
        Optional[Union[str, Tuple[str, str]]]:
        - None: Use default directive (no override)
        - str: Replace default directive with this string
        - Tuple[str, str]: First element is mode ("append" or "replace"), second is directive string

    Example:
        @input_directive_override('user_email')
        async def custom_email_directive(
            field_name: str,
            value: str,
            session: InterviewSession,
            interaction: Interaction,
            visitor: InteractWalker
        ) -> Optional[Union[str, Tuple[str, str]]]:
            if '@example.com' in value:
                return "Tell the user: Thank you for using your work email!"
            return None  # Use default directive
    """
    def decorator(func: Callable) -> Callable:
        # Store the question name on the function for later lookup
        func._interview_question_name = question_name  # type: ignore
        func._interview_handler_type = "input_directive_override"  # type: ignore

        # Try to determine the interview_type from the module where this function is defined
        # or use the provided interview_type parameter
        determined_type = interview_type
        try:
            if not determined_type:
                module = inspect.getmodule(func)
                if module:
                    # Import here to avoid circular dependency
                    from jvagent.action.interview.interview_interact_action import InterviewInteractAction
                    # Look for InterviewInteractAction subclasses in the module
                    for name, obj in vars(module).items():
                        if (inspect.isclass(obj) and
                            issubclass(obj, InterviewInteractAction) and
                            obj is not InterviewInteractAction):
                            determined_type = obj.__name__
                            break
            
            if determined_type:
                # Register in module-level registry
                _input_directive_override_registry[(determined_type, question_name)] = func
            else:
                # Store in pending registry if interview_type is provided but class not yet defined
                # Otherwise, rely on class attribute scanning in __init_subclass__
                if interview_type:
                    if interview_type not in _pending_input_directive_overrides:
                        _pending_input_directive_overrides[interview_type] = {}
                    _pending_input_directive_overrides[interview_type][question_name] = func
        except Exception as e:
            logger.warning(f"Error registering directive override '{func.__name__}': {e}")

        return func
    return decorator


def on_interview_complete(interview_type: str):
    """Decorator to register a completion handler for a specific interview type.

    Completion handlers are called when an interview session reaches the COMPLETED state.
    Use this to process collected data, trigger downstream actions, or perform cleanup.

    Args:
        interview_type: Class name of the InterviewInteractAction (e.g., 'SignupInterviewInteractAction')

    Handler Signature:
        The handler must accept three parameters:
        - session: InterviewSession - The completed interview session with all collected responses
        - visitor: InteractWalker - The walker for accessing context and responding
        - action: InteractAction - The action instance (use action.respond() to send responses)

    Example:
        @on_interview_complete('SignupInterviewInteractAction')
        async def handle_signup_completion(
            session: InterviewSession,
            visitor: InteractWalker,
            action: InteractAction
        ) -> None:
            # Process collected data
            user_name = session.responses.get('user_name')
            user_email = session.responses.get('user_email')
            # Send response using action.respond()
            await action.respond(visitor, directives=["Thank you for signing up!"])
            # Trigger downstream actions, send notifications, etc.
    """
    def decorator(func: Callable) -> Callable:
        # Register the handler in the module-level registry
        _completion_handlers[interview_type] = func
        return func
    return decorator


# Export registry access functions for InterviewInteractAction
def get_completion_handler(interview_type: str) -> Optional[Callable]:
    """Get completion handler for an interview type."""
    return _completion_handlers.get(interview_type)


def get_input_handler(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input handler for a question."""
    return _input_handler_registry.get((interview_type, question_name))


def get_input_validator(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input validator for a question."""
    return _input_validator_registry.get((interview_type, question_name))


def get_input_directive_override(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input directive override for a question."""
    return _input_directive_override_registry.get((interview_type, question_name))


def get_pending_input_handlers(interview_type: str) -> Dict[str, Callable]:
    """Get pending input handlers for an interview type."""
    return _pending_input_handlers.get(interview_type, {})


def get_pending_input_validators(interview_type: str) -> Dict[str, Callable]:
    """Get pending input validators for an interview type."""
    return _pending_input_validators.get(interview_type, {})


def get_pending_input_directive_overrides(interview_type: str) -> Dict[str, Callable]:
    """Get pending input directive overrides for an interview type."""
    return _pending_input_directive_overrides.get(interview_type, {})


def clear_pending_registrations(interview_type: str) -> None:
    """Clear pending registrations for an interview type after class is defined."""
    _pending_input_handlers.pop(interview_type, None)
    _pending_input_validators.pop(interview_type, None)
    _pending_input_directive_overrides.pop(interview_type, None)
    _pending_branch_functions.pop(interview_type, None)


def branch_function(function_name: str, interview_type: Optional[str] = None):
    """Decorator to register a branch function for conditional branching.

    Branch functions evaluate complex conditions with full access to session and visitor.
    They can return bool (direct branching) or any value (for operator evaluation).

    Args:
        function_name: Unique name for this branch function
        interview_type: Optional interview type (auto-detected from module if not provided)

    Function Signature:
        def function_name(session: InterviewSession, visitor: InteractWalker) -> Union[bool, Any]:
            # Return bool for direct branching, or any value for operator evaluation
            pass

    Example:
        @branch_function('check_similarity')
        async def check_similarity(session: InterviewSession, visitor: InteractWalker) -> bool:
            description = session.responses.get('report_description', '')
            # Complex logic with visitor access
            return similarity_score > 0.8
    """
    def decorator(func: Callable) -> Callable:
        # Store metadata on function
        func._interview_question_name = function_name  # type: ignore
        func._interview_handler_type = "branch_function"  # type: ignore

        # Auto-detect interview_type from module
        determined_type = interview_type
        try:
            if not determined_type:
                module = inspect.getmodule(func)
                if module:
                    # Import here to avoid circular dependency
                    from jvagent.action.interview.interview_interact_action import InterviewInteractAction
                    # Look for InterviewInteractAction subclasses in the module
                    for name, obj in vars(module).items():
                        if (inspect.isclass(obj) and
                            issubclass(obj, InterviewInteractAction) and
                            obj is not InterviewInteractAction):
                            determined_type = obj.__name__
                            break

            if determined_type:
                # Register in module-level registry
                _branch_function_registry[(determined_type, function_name)] = func
            else:
                # Store in pending registry if interview_type is provided but class not yet defined
                if interview_type:
                    if interview_type not in _pending_branch_functions:
                        _pending_branch_functions[interview_type] = {}
                    _pending_branch_functions[interview_type][function_name] = func
        except Exception as e:
            logger.warning(f"Error registering branch function '{func.__name__}': {e}")

        return func
    return decorator


def get_branch_function(interview_type: str, function_name: str) -> Optional[Callable]:
    """Get registered branch function.

    Args:
        interview_type: Interview type (class name)
        function_name: Name of the branch function

    Returns:
        Registered function if found, None otherwise
    """
    return _branch_function_registry.get((interview_type, function_name))


def get_pending_branch_functions(interview_type: str) -> Dict[str, Callable]:
    """Get pending branch functions for an interview type.

    Args:
        interview_type: Interview type (class name)

    Returns:
        Dictionary of function_name -> function for pending registrations
    """
    return _pending_branch_functions.get(interview_type, {})
