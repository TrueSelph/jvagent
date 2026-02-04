"""Decorators for interview action extensions.

This module provides decorators for registering custom handlers, validators,
directive overrides, and completion handlers for interview actions.
"""

from __future__ import annotations

import inspect
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

if TYPE_CHECKING:
    from jvagent.action.interview.core.session.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.base import InteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)

# Thread lock for registry access
_registry_lock = threading.RLock()

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

# Module-level registry for context data provider functions
# Format: {(interview_type, function_name): function}
_input_context_provider_registry: Dict[Tuple[str, str], Callable] = {}
_pending_input_context_providers: Dict[str, Dict[str, Callable]] = {}

# Module-level registry for review value override (one per interview type)
# Keyed by interview_type. Pending keyed by module __name__ when class not yet defined.
_input_review_override_registry: Dict[str, Callable] = {}
_pending_input_review_overrides: Dict[str, Callable] = {}


def _detect_interview_type(func: Callable, interview_type: Optional[str] = None) -> Optional[str]:
    """Detect the interview type from the function's module or use provided type.
    
    Args:
        func: The function being decorated
        interview_type: Optional explicitly provided interview type
        
    Returns:
        The determined interview type, or None if not found
    """
    if interview_type:
        return interview_type
    
    try:
        module = inspect.getmodule(func)
        if module:
            # Import here to avoid circular dependency
            from jvagent.action.interview.interview_interact_action import InterviewInteractAction
            # Look for InterviewInteractAction subclasses in the module
            for name, obj in vars(module).items():
                if (inspect.isclass(obj) and
                    issubclass(obj, InterviewInteractAction) and
                    obj is not InterviewInteractAction):
                    return obj.__name__
    except Exception as e:
        logger.debug(f"Could not detect interview type for '{func.__name__}': {e}")
    
    return None


def _register_decorator_function(
    func: Callable,
    identifier: str,
    handler_type: str,
    interview_type: Optional[str],
    registry: Dict[Tuple[str, str], Callable],
    pending_registry: Dict[str, Dict[str, Callable]]
) -> None:
    """Common registration logic for all decorators.
    
    Thread-safe registration using a module-level lock.
    
    Args:
        func: The function being decorated
        identifier: Question name or function name
        handler_type: Type of handler (e.g., 'input_handler', 'input_validator')
        interview_type: Optional explicitly provided interview type
        registry: Main registry dictionary
        pending_registry: Pending registry for functions decorated before class definition
    """
    # Store metadata on the function
    func._interview_question_name = identifier  # type: ignore
    func._interview_handler_type = handler_type  # type: ignore
    
    try:
        # Determine the interview type
        determined_type = _detect_interview_type(func, interview_type)
        
        with _registry_lock:
            if determined_type:
                # Register in module-level registry
                registry[(determined_type, identifier)] = func
                logger.debug(f"Registered {handler_type} '{identifier}' for interview type '{determined_type}'")
            else:
                # Store in pending registry if interview_type is provided but class not yet defined
                # Otherwise, rely on class attribute scanning in __init_subclass__
                if interview_type:
                    if interview_type not in pending_registry:
                        pending_registry[interview_type] = {}
                    pending_registry[interview_type][identifier] = func
                    logger.debug(f"Stored {handler_type} '{identifier}' in pending registry for '{interview_type}'")
    except Exception as e:
        logger.warning(f"Error registering {handler_type} '{func.__name__}': {e}")


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
        _register_decorator_function(
            func,
            question_name,
            "input_handler",
            interview_type,
            _input_handler_registry,
            _pending_input_handlers
        )
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
        _register_decorator_function(
            func,
            question_name,
            "input_validator",
            interview_type,
            _input_validator_registry,
            _pending_input_validators
        )
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
        _register_decorator_function(
            func,
            question_name,
            "input_directive_override",
            interview_type,
            _input_directive_override_registry,
            _pending_input_directive_overrides
        )
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
        with _registry_lock:
            _completion_handlers[interview_type] = func
        return func
    return decorator


def input_review_override(func: Callable) -> Callable:
    """Decorator to register a review values override for the interview action in this module.

    No parameters. Applies only to the InterviewInteractAction subclass defined in the same
    module. The decorated function receives a key-value map of collected interview data
    (field name to value) for display only; modifications must not alter the session's
    stored values.

    Handler signature: (session: InterviewSession, data: Dict[str, Any]) -> Dict[str, Any]
    (sync or async). Omit fields by dropping keys; format by changing values in the
    returned dict. The session's stored values are never modified.

    Example:
        @input_review_override
        def adapt_review(session: InterviewSession, data: Dict[str, Any]) -> Dict[str, Any]:
            return {k: v for k, v in data.items() if v not in (None, "", "n/a")}
    """
    func._interview_question_name = "__review_override__"  # type: ignore
    func._interview_handler_type = "input_review_override"  # type: ignore

    try:
        determined_type = _detect_interview_type(func, None)
        with _registry_lock:
            if determined_type:
                _input_review_override_registry[determined_type] = func
                logger.debug(
                    f"Registered input_review_override for interview type '{determined_type}'"
                )
            else:
                module = inspect.getmodule(func)
                if module:
                    _pending_input_review_overrides[module.__name__] = func
                    logger.debug(
                        f"Stored input_review_override in pending registry for module '{module.__name__}'"
                    )
    except Exception as e:
        logger.warning(f"Error registering input_review_override '{func.__name__}': {e}")

    return func


# Export registry access functions for InterviewInteractAction
def get_completion_handler(interview_type: str) -> Optional[Callable]:
    """Get completion handler for an interview type (thread-safe)."""
    with _registry_lock:
        return _completion_handlers.get(interview_type)


def get_input_handler(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input handler for a question (thread-safe)."""
    with _registry_lock:
        return _input_handler_registry.get((interview_type, question_name))


def get_input_validator(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input validator for a question (thread-safe)."""
    with _registry_lock:
        return _input_validator_registry.get((interview_type, question_name))


def get_input_directive_override(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input directive override for a question (thread-safe)."""
    with _registry_lock:
        return _input_directive_override_registry.get((interview_type, question_name))


def get_input_review_override(interview_type: str) -> Optional[Callable]:
    """Get input review override for an interview type (thread-safe)."""
    with _registry_lock:
        return _input_review_override_registry.get(interview_type)


def get_pending_input_handlers(interview_type: str) -> Dict[str, Callable]:
    """Get pending input handlers for an interview type (thread-safe)."""
    with _registry_lock:
        return _pending_input_handlers.get(interview_type, {}).copy()


def get_pending_input_validators(interview_type: str) -> Dict[str, Callable]:
    """Get pending input validators for an interview type (thread-safe)."""
    with _registry_lock:
        return _pending_input_validators.get(interview_type, {}).copy()


def get_pending_input_directive_overrides(interview_type: str) -> Dict[str, Callable]:
    """Get pending input directive overrides for an interview type (thread-safe)."""
    with _registry_lock:
        return _pending_input_directive_overrides.get(interview_type, {}).copy()


def flush_module_registrations_for_class(interview_type: str, module: Any) -> None:
    """Register module-level input_context_provider and branch_function from module.

    When these decorators run before the action class is defined, _detect_interview_type
    returns None so they are not registered. This scans the class's module and registers
    any such decorated functions under the given interview_type (thread-safe).
    """
    if module is None:
        return
    with _registry_lock:
        for _name, obj in vars(module).items():
            if not callable(obj) or not hasattr(obj, "_interview_handler_type"):
                continue
            handler_type = getattr(obj, "_interview_handler_type", None)
            identifier = getattr(obj, "_interview_question_name", None)
            if not identifier:
                continue
            if handler_type == "input_context_provider":
                if (interview_type, identifier) not in _input_context_provider_registry:
                    _input_context_provider_registry[(interview_type, identifier)] = obj
                    logger.debug(
                        f"Registered input_context_provider '{identifier}' for interview type '{interview_type}' (from module scan)"
                    )
            elif handler_type == "input_review_override":
                if interview_type not in _input_review_override_registry:
                    _input_review_override_registry[interview_type] = obj
                    logger.debug(
                        f"Registered input_review_override for interview type '{interview_type}' (from module scan)"
                    )
            elif handler_type == "branch_function":
                if (interview_type, identifier) not in _branch_function_registry:
                    _branch_function_registry[(interview_type, identifier)] = obj
                    logger.debug(
                        f"Registered branch_function '{identifier}' for interview type '{interview_type}' (from module scan)"
                    )


def clear_pending_registrations(interview_type: str, module_name: Optional[str] = None) -> None:
    """Clear pending registrations for an interview type after class is defined (thread-safe).

    If module_name is provided, any pending input_review_override for that module is
    registered under interview_type and then removed from pending.
    """
    with _registry_lock:
        _pending_input_handlers.pop(interview_type, None)
        _pending_input_validators.pop(interview_type, None)
        _pending_input_directive_overrides.pop(interview_type, None)
        _pending_branch_functions.pop(interview_type, None)
        _pending_input_context_providers.pop(interview_type, None)
        if module_name is not None and module_name in _pending_input_review_overrides:
            _input_review_override_registry[interview_type] = _pending_input_review_overrides.pop(
                module_name
            )


def branch_function(function_name: Optional[str] = None, interview_type: Optional[str] = None):
    """Decorator to register a branch function for conditional branching.

    Branch functions evaluate complex conditions with full access to session and visitor.
    They can return bool (direct branching) or any value (for operator evaluation).

    Args:
        function_name: Optional unique name for this branch function. If not provided, uses the function's __name__
        interview_type: Optional interview type (auto-detected from module if not provided)

    Function Signature:
        def function_name(session: InterviewSession, visitor: InteractWalker) -> Union[bool, Any]:
            # Return bool for direct branching, or any value for operator evaluation
            pass

    Examples:
        # Name automatically derived from function name
        @branch_function()
        async def check_similarity(session: InterviewSession, visitor: InteractWalker) -> bool:
            description = session.responses.get('report_description', '')
            return similarity_score > 0.8
        
        # Explicit name (optional, for backward compatibility)
        @branch_function('check_similarity')
        async def check_similarity(session: InterviewSession, visitor: InteractWalker) -> bool:
            return similarity_score > 0.8
    """
    def decorator(func: Callable) -> Callable:
        # Use function name if not explicitly provided
        name = function_name if function_name is not None else func.__name__
        _register_decorator_function(
            func,
            name,
            "branch_function",
            interview_type,
            _branch_function_registry,
            _pending_branch_functions
        )
        return func
    return decorator


def get_branch_function(interview_type: str, function_name: str) -> Optional[Callable]:
    """Get registered branch function (thread-safe).

    Args:
        interview_type: Interview type (class name)
        function_name: Name of the branch function

    Returns:
        Registered function if found, None otherwise
    """
    with _registry_lock:
        return _branch_function_registry.get((interview_type, function_name))


def get_pending_branch_functions(interview_type: str) -> Dict[str, Callable]:
    """Get pending branch functions for an interview type (thread-safe).

    Args:
        interview_type: Interview type (class name)

    Returns:
        Dictionary of function_name -> function for pending registrations
    """
    with _registry_lock:
        return _pending_branch_functions.get(interview_type, {}).copy()


def input_context_provider(function_name: Optional[str] = None, interview_type: Optional[str] = None):
    """Decorator to register an input context provider function.

    Input context provider functions supply dynamic context data to questions
    (e.g., available times, valid options, personalized choices) at the time
    the question is presented to the user.

    Args:
        function_name: Optional unique name for this input context provider. If not provided, uses the function's __name__
        interview_type: Optional interview type (auto-detected from module if not provided)

    Function Signature:
        async def function_name(session: InterviewSession, visitor: InteractWalker) -> Dict[str, Any]:
            # Return dictionary of context data to be included in question prompt
            pass

    Examples:
        # Name automatically derived from function name (recommended)
        @input_context_provider()
        async def get_available_times(session: InterviewSession, visitor: InteractWalker) -> Dict[str, Any]:
            times = await fetch_calendar_availability()
            return {
                "available_times": times,
                "timezone": "America/New_York"
            }
        
        # Explicit name (optional, for backward compatibility)
        @input_context_provider('get_available_times')
        async def get_available_times(session: InterviewSession, visitor: InteractWalker) -> Dict[str, Any]:
            return {"available_times": times}
    """
    def decorator(func: Callable) -> Callable:
        # Use function name if not explicitly provided
        name = function_name if function_name is not None else func.__name__
        _register_decorator_function(
            func,
            name,
            "input_context_provider",
            interview_type,
            _input_context_provider_registry,
            _pending_input_context_providers
        )
        return func
    return decorator


def get_input_context_provider(interview_type: str, function_name: str) -> Optional[Callable]:
    """Get registered input context provider function (thread-safe).

    Args:
        interview_type: Interview type (class name)
        function_name: Name of the input context provider function

    Returns:
        Registered function if found, None otherwise
    """
    with _registry_lock:
        return _input_context_provider_registry.get((interview_type, function_name))


def get_pending_input_context_providers(interview_type: str) -> Dict[str, Callable]:
    """Get pending input context providers for an interview type (thread-safe).

    Args:
        interview_type: Interview type (class name)

    Returns:
        Dictionary of function_name -> function for pending registrations
    """
    with _registry_lock:
        return _pending_input_context_providers.get(interview_type, {}).copy()
