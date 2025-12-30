"""Interview Interact Action Package

This package provides the InterviewInteractAction class and its state machine components.
"""

# Import the action class and decorators so they can be imported from the package
from .interview_interact_action import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    on_interview_complete,
)

# Import core components
from .core import (
    InterviewSession,
    QuestionNode,
    InterviewState,
    ValidationStatus,
)

# Import state actions
from .states import (
    ActiveStateInteractAction,
    ReviewStateInteractAction,
    CompletedStateInteractAction,
    CancelledStateInteractAction,
)

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
# from . import endpoints  # noqa: F401

__all__ = [
    "InterviewInteractAction",
    "input_handler",
    "input_validator",
    "on_interview_complete",
    "InterviewSession",
    "QuestionNode",
    "InterviewState",
    "ValidationStatus",
    "ActiveStateInteractAction",
    "ReviewStateInteractAction",
    "CompletedStateInteractAction",
    "CancelledStateInteractAction",
]
