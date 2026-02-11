"""Utilities for invoking interview handlers with optional context (visitor, interview_action).

Supports backward compatibility: handlers that do not accept visitor or interview_action
continue to work; those that do can receive them when the caller provides them.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


def invoke_with_optional_context(
    func: Callable[..., Any],
    *args: Any,
    visitor: Optional[Any] = None,
    interview_action: Optional[Any] = None,
    **kwargs: Any,
) -> Any:
    """Invoke a callable with optional visitor and interview_action if it accepts them.

    Inspects the callable's signature and only passes visitor and interview_action
    when the callable has parameters with those names. This keeps existing handlers
    (that take fewer arguments) working unchanged.

    Args:
        func: The callable to invoke (sync or async; caller must await if async).
        *args: Positional arguments to pass to the callable.
        visitor: Optional InteractWalker to pass if the callable accepts 'visitor'.
        interview_action: Optional InterviewInteractAction to pass if the callable accepts 'interview_action'.
        **kwargs: Keyword arguments to pass to the callable.

    Returns:
        Whatever the callable returns. Caller must await if func is async.
    """
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return func(*args, **kwargs)

    params = sig.parameters
    merged = dict(kwargs)
    if "visitor" in params and visitor is not None:
        merged["visitor"] = visitor
    if "interview_action" in params and interview_action is not None:
        merged["interview_action"] = interview_action

    return func(*args, **merged)


async def invoke_async_with_optional_context(
    func: Callable[..., Any],
    *args: Any,
    visitor: Optional[Any] = None,
    interview_action: Optional[Any] = None,
    **kwargs: Any,
) -> Any:
    """Invoke an async or sync callable with optional visitor and interview_action.

    If the callable is async, awaits it; otherwise calls it and returns the result.
    """
    result = invoke_with_optional_context(
        func, *args, visitor=visitor, interview_action=interview_action, **kwargs
    )
    if inspect.iscoroutine(result):
        return await result
    return result
