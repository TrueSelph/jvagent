"""Decorators for interview action extensions.

This module provides decorators for registering custom handlers, validators,
directive overrides, and completion handlers for interview actions.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from jvagent.action.interview.core.session.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.base import InteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)

# Thread-safe context variable for tracking response accesses during branch function execution
# Used by the dependency tracking wrapper to capture which responses were accessed
_response_access_tracker: contextvars.ContextVar[Optional[Set[str]]] = contextvars.ContextVar(
    '_response_access_tracker',
    default=None
)


@contextmanager
def track_response_access():
    """Context manager to track which response keys are accessed during execution.
    
    Usage:
        with track_response_access() as tracked:
            # ... execute code that accesses session.responses ...
            accessed_keys = tracked.get()  # Returns set of accessed keys
    
    Yields:
        Object with get() method returning set of accessed response keys
    """
    accessed = set()
    token = _response_access_tracker.set(accessed)
    try:
        class AccessTracker:
            def get(self_inner):
                return accessed

        yield AccessTracker()
    finally:
        _response_access_tracker.reset(token)


def get_tracked_responses() -> Optional[Set[str]]:
    """Get the current response access tracker if one is active.
    
    Returns:
        Set of accessed response keys if tracking is active, None otherwise
    """
    return _response_access_tracker.get()


def record_response_access(key: str) -> None:
    """Record access to a response key during tracking.
    
    Called by instrumented session.responses access to track which keys are read.
    
    Args:
        key: Response key being accessed
    """
    tracker = _response_access_tracker.get()
    if tracker is not None:
        tracker.add(key)


# Thread lock for registry access
_registry_lock = threading.RLock()
# Centralized storage for all module-level registries
_registries: Dict[str, Any] = {
    "completion_handlers": {},  # interview_type -> callable
    "cancelled_handlers": {},  # interview_type -> callable
    "review_handlers": {},  # interview_type -> callable
    "input_handler": {},  # (interview_type, question_name) -> callable
    "input_validator": {},  # (interview_type, question_name) -> callable
    "input_directive_override": {},  # (interview_type, question_name) -> callable
    "pending_input_handlers": {},  # interview_type -> {question_name: callable}
    "pending_input_validators": {},
    "pending_input_directive_overrides": {},
    "branch_function": {},  # (interview_type, name) -> callable
    "pending_branch_functions": {},
    "input_context_provider": {},  # (interview_type, name) -> callable
    "pending_input_context_providers": {},
    "input_review_override": {},  # interview_type -> callable
    "pending_input_review_overrides": {},  # module_name -> callable
}

# Backwards-compatible aliases for existing variable names used across the codebase
_completion_handlers = _registries["completion_handlers"]
_cancelled_handlers = _registries["cancelled_handlers"]
_review_handlers = _registries["review_handlers"]
_input_handler_registry = _registries["input_handler"]
_input_validator_registry = _registries["input_validator"]
_input_directive_override_registry = _registries["input_directive_override"]
_pending_input_handlers = _registries["pending_input_handlers"]
_pending_input_validators = _registries["pending_input_validators"]
_pending_input_directive_overrides = _registries["pending_input_directive_overrides"]
_branch_function_registry = _registries["branch_function"]
_pending_branch_functions = _registries["pending_branch_functions"]
_input_context_provider_registry = _registries["input_context_provider"]
_pending_input_context_providers = _registries["pending_input_context_providers"]
_input_review_override_registry = _registries["input_review_override"]
_pending_input_review_overrides = _registries["pending_input_review_overrides"]


class RegistryManager:
    """Thread-safe access helper for centralized registries."""

    @staticmethod
    def get_completion_handler(interview_type: str):
        with _registry_lock:
            return _registries["completion_handlers"].get(interview_type)

    @staticmethod
    def set_completion_handler(interview_type: str, func: Callable):
        with _registry_lock:
            _registries["completion_handlers"][interview_type] = func

    @staticmethod
    def get_cancelled_handler(interview_type: str):
        with _registry_lock:
            return _registries["cancelled_handlers"].get(interview_type)

    @staticmethod
    def set_cancelled_handler(interview_type: str, func: Callable):
        with _registry_lock:
            _registries["cancelled_handlers"][interview_type] = func

    @staticmethod
    def get_review_handler(interview_type: str):
        with _registry_lock:
            return _registries["review_handlers"].get(interview_type)

    @staticmethod
    def set_review_handler(interview_type: str, func: Callable):
        with _registry_lock:
            _registries["review_handlers"][interview_type] = func

    @staticmethod
    def get_input_handler(interview_type: str, question_name: str):
        with _registry_lock:
            return _registries["input_handler"].get((interview_type, question_name))

    @staticmethod
    def set_input_handler(interview_type: str, question_name: str, func: Callable):
        with _registry_lock:
            _registries["input_handler"][(interview_type, question_name)] = func

    @staticmethod
    def get_input_validator(interview_type: str, question_name: str):
        with _registry_lock:
            return _registries["input_validator"].get((interview_type, question_name))

    @staticmethod
    def set_input_validator(interview_type: str, question_name: str, func: Callable):
        with _registry_lock:
            _registries["input_validator"][(interview_type, question_name)] = func

    @staticmethod
    def get_input_directive_override(interview_type: str, question_name: str):
        with _registry_lock:
            return _registries["input_directive_override"].get((interview_type, question_name))

    @staticmethod
    def set_input_directive_override(interview_type: str, question_name: str, func: Callable):
        with _registry_lock:
            _registries["input_directive_override"][(interview_type, question_name)] = func

    @staticmethod
    def get_input_review_override(interview_type: str):
        with _registry_lock:
            return _registries["input_review_override"].get(interview_type)

    @staticmethod
    def set_input_review_override(interview_type: str, func: Callable):
        with _registry_lock:
            _registries["input_review_override"][interview_type] = func

    @staticmethod
    def get_pending(registry_name: str, interview_type: str):
        with _registry_lock:
            return _registries.get(registry_name, {}).get(interview_type, {}).copy()

    @staticmethod
    def set_pending(registry_name: str, interview_type: str, data: Dict[str, Callable]):
        with _registry_lock:
            _registries[registry_name][interview_type] = data

    @staticmethod
    def register_branch_function(interview_type: str, function_name: str, func: Callable):
        with _registry_lock:
            _registries["branch_function"][(interview_type, function_name)] = func

    @staticmethod
    def register_input_context_provider(interview_type: str, function_name: str, func: Callable):
        with _registry_lock:
            _registries["input_context_provider"][(interview_type, function_name)] = func



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

    Handler signature (recommended): (raw_input, session, interaction, visitor=None, interview_action=None).
    visitor and interview_action are passed only when the callable accepts them (backward compatible).

    Args:
        question_name: Name of the question (must match 'name' field in question_graph)

    Example:
        @input_handler('available_times')
        async def normalize_time(raw_input: str, session: InterviewSession, interaction: Interaction,
                                 visitor=None, interview_action=None) -> str:
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

    Handler signature (recommended): (value, session, visitor=None, interview_action=None).
    visitor and interview_action are passed only when the callable accepts them (backward compatible).

    Args:
        question_name: Name of the question (must match 'name' field in question_graph)

    Example:
        @input_validator('user_email')
        def validate_email(value: str, session: InterviewSession,
                           visitor=None, interview_action=None) -> Tuple[ValidationStatus, Optional[str]]:
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

    Handler signature (recommended): (field_name, value, session, interaction, visitor, interview_action).
    When invoked, visitor and interview_action are passed for consistency with other handlers.

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


def on_interview_cancelled(interview_type: str):
    """Decorator to register a cancellation handler for a specific interview type.

    Cancellation handlers are called when an interview session reaches the CANCELLED state.
    Use this to perform cleanup, log cancellation reasons, or trigger follow-up actions.

    Args:
        interview_type: Class name of the InterviewInteractAction (e.g., 'SignupInterviewInteractAction')

    Handler Signature:
        The handler must accept three parameters:
        - session: InterviewSession - The cancelled interview session with partial responses
        - visitor: InteractWalker - The walker for accessing context and responding
        - action: InteractAction - The action instance (use action.respond() to send responses)

    Example:
        @on_interview_cancelled('SignupInterviewInteractAction')
        async def handle_signup_cancellation(
            session: InterviewSession,
            visitor: InteractWalker,
            action: InteractAction
        ) -> None:
            # Log cancellation for analytics
            logger.info(f"Signup cancelled at question: {session.current_question}")
            # Optionally save partial data
            partial_data = session.responses
            # Send custom cancellation message
            await action.respond(visitor, directives=["No problem! Feel free to start again anytime."])
    """
    def decorator(func: Callable) -> Callable:
        with _registry_lock:
            _cancelled_handlers[interview_type] = func
        return func
    return decorator


def on_interview_review(interview_type: str):
    """Decorator to register a review handler for a specific interview type.

    Review handlers are called when an interview session reaches the REVIEW state.
    Use this to customize the review experience, add additional context, or perform
    pre-completion validation.

    Note: This handler is called BEFORE the review summary is shown to the user.
    The handler can modify how data is presented or add additional directives.

    Args:
        interview_type: Class name of the InterviewInteractAction (e.g., 'SignupInterviewInteractAction')

    Handler Signature:
        The handler must accept three parameters:
        - session: InterviewSession - The interview session with all collected responses
        - visitor: InteractWalker - The walker for accessing context and responding
        - action: InteractAction - The action instance

    Returns:
        Optional[str]: Custom directive to prepend to the review summary, or None to use default.

    Example:
        @on_interview_review('SignupInterviewInteractAction')
        async def handle_signup_review(
            session: InterviewSession,
            visitor: InteractWalker,
            action: InteractAction
        ) -> Optional[str]:
            # Add personalized review introduction
            user_name = session.responses.get('user_name', 'there')
            return f"Great job, {user_name}! Let's review your information."
    """
    def decorator(func: Callable) -> Callable:
        with _registry_lock:
            _review_handlers[interview_type] = func
        return func
    return decorator


def input_review_override(func: Callable) -> Callable:
    """Decorator to register a review values override for the interview action in this module.

    No parameters. Applies only to the InterviewInteractAction subclass defined in the same
    module. The decorated function receives a key-value map of collected interview data
    (field name to value) for display only; modifications must not alter the session's
    stored values.

    Handler signature: (session, data) or (session, data, visitor=None, interview_action=None).
    visitor and interview_action are passed only when the callable accepts them (backward compatible).
    Return Dict[str, Any]; session storage is never modified.

    Example:
        @input_review_override
        def adapt_review(session: InterviewSession, data: Dict[str, Any],
                        visitor=None, interview_action=None) -> Dict[str, Any]:
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
    return RegistryManager.get_completion_handler(interview_type)


def get_cancelled_handler(interview_type: str) -> Optional[Callable]:
    """Get cancellation handler for an interview type (thread-safe)."""
    return RegistryManager.get_cancelled_handler(interview_type)


def get_review_handler(interview_type: str) -> Optional[Callable]:
    """Get review handler for an interview type (thread-safe)."""
    return RegistryManager.get_review_handler(interview_type)


def get_input_handler(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input handler for a question (thread-safe)."""
    return RegistryManager.get_input_handler(interview_type, question_name)


def get_input_validator(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input validator for a question (thread-safe)."""
    return RegistryManager.get_input_validator(interview_type, question_name)


def get_input_directive_override(interview_type: str, question_name: str) -> Optional[Callable]:
    """Get input directive override for a question (thread-safe)."""
    return RegistryManager.get_input_directive_override(interview_type, question_name)


def get_input_review_override(interview_type: str) -> Optional[Callable]:
    """Get input review override for an interview type (thread-safe)."""
    return RegistryManager.get_input_review_override(interview_type)


def get_pending_input_handlers(interview_type: str) -> Dict[str, Callable]:
    """Get pending input handlers for an interview type (thread-safe)."""
    return RegistryManager.get_pending("pending_input_handlers", interview_type)


def get_pending_input_validators(interview_type: str) -> Dict[str, Callable]:
    """Get pending input validators for an interview type (thread-safe)."""
    return RegistryManager.get_pending("pending_input_validators", interview_type)


def get_pending_input_directive_overrides(interview_type: str) -> Dict[str, Callable]:
    """Get pending input directive overrides for an interview type (thread-safe)."""
    return RegistryManager.get_pending("pending_input_directive_overrides", interview_type)


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
    
    Functions automatically have dependency tracking enabled: response keys accessed
    during execution are recorded and used for efficient caching and invalidation.

    Args:
        function_name: Optional unique name for this branch function. If not provided, uses the function's __name__
        interview_type: Optional interview type (auto-detected from module if not provided)

    Function Signature:
        def function_name(session: InterviewSession, visitor: InteractWalker) -> Union[bool, Any]:
            # Return bool for direct branching, or any value (for operator evaluation)
            # Accessed responses are automatically tracked for cache invalidation
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
        
        # Wrap function with dependency tracking (only if not already wrapped)
        if not hasattr(func, '_branch_function_wrapped'):
            wrapped_func = _wrap_branch_function_with_tracking(func)
            wrapped_func._branch_function_wrapped = True
            wrapped_func._original_function = func
        else:
            wrapped_func = func
        
        _register_decorator_function(
            wrapped_func,
            name,
            "branch_function",
            interview_type,
            _branch_function_registry,
            _pending_branch_functions
        )
        return wrapped_func
    return decorator


def _wrap_branch_function_with_tracking(func: Callable) -> Callable:
    """Wrap a branch function to automatically track response accesses.
    
    Creates a wrapper that instruments session.responses access to record
    which response keys are read during function execution.
    
    Args:
        func: The branch function to wrap
        
    Returns:
        Wrapped function that tracks response dependencies
    """
    if inspect.iscoroutinefunction(func):
        async def async_wrapper(session: "InterviewSession", visitor: "InteractWalker") -> Any:
            if visitor is None:
                raise ValueError("branch function requires a visitor to be provided")
            with track_response_access() as tracker:
                # Instrument session.responses for this call
                original_responses = session.responses
                instrumented = _InstrumentedResponses(original_responses)
                session.responses = instrumented  # type: ignore
                
                try:
                    result = await func(session, visitor)
                    # Store accessed keys in context for branch evaluator to retrieve
                    if not hasattr(session, '_branch_function_accessed_keys'):
                        session._branch_function_accessed_keys = set()  # type: ignore
                    session._branch_function_accessed_keys.update(tracker.get())  # type: ignore
                    return result
                finally:
                    session.responses = original_responses
        
        return async_wrapper
    else:
        def sync_wrapper(session: "InterviewSession", visitor: "InteractWalker") -> Any:
            if visitor is None:
                raise ValueError("branch function requires a visitor to be provided")
            with track_response_access() as tracker:
                # Instrument session.responses for this call
                original_responses = session.responses
                instrumented = _InstrumentedResponses(original_responses)
                session.responses = instrumented  # type: ignore
                
                try:
                    result = func(session, visitor)
                    # Store accessed keys in context for branch evaluator to retrieve
                    if not hasattr(session, '_branch_function_accessed_keys'):
                        session._branch_function_accessed_keys = set()  # type: ignore
                    session._branch_function_accessed_keys.update(tracker.get())  # type: ignore
                    return result
                finally:
                    session.responses = original_responses
        
        return sync_wrapper


class _InstrumentedResponses(dict):
    """Dictionary subclass that tracks access to response keys.
    
    When a key is accessed (via get, __getitem__, etc.), records it
    in the active response access tracker.
    """
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get with access tracking."""
        record_response_access(key)
        return super().get(key, default)
    
    def __getitem__(self, key: str) -> Any:
        """Index access with tracking."""
        record_response_access(key)
        return super().__getitem__(key)
    
    def __contains__(self, key: Any) -> bool:
        """Containment check with tracking."""
        if isinstance(key, str):
            record_response_access(key)
        return super().__contains__(key)


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


def register_branch_function(interview_type: str, function_name: str, func: Callable) -> None:
    """Register a branch function programmatically (thread-safe).

    This can be used by classes that define branch functions as methods
    (so the decorator ran at class body execution time) to ensure the
    module-level registry contains the function under the given
    interview_type.
    """
    RegistryManager.register_branch_function(interview_type, function_name, func)
    logger.debug(
        f"Registered branch_function '{function_name}' for interview type '{interview_type}' (programmatic)"
    )


def register_input_context_provider(interview_type: str, function_name: str, func: Callable) -> None:
    """Register an input context provider programmatically (thread-safe)."""
    RegistryManager.register_input_context_provider(interview_type, function_name, func)
    logger.debug(
        f"Registered input_context_provider '{function_name}' for interview type '{interview_type}' (programmatic)"
    )


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

    Function signature (recommended): (session, visitor, interview_action=None) -> Dict[str, Any].
    interview_action is passed only when the callable accepts it (backward compatible).

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
