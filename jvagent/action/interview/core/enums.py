"""Enums and constants for interview action."""

from enum import Enum


class InterviewState(str, Enum):
    """Interview session state machine states."""
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ValidationStatus(str, Enum):
    """Two-tier validation status for question responses.
    
    VALID responses can optionally include a feedback message for clarification.
    """
    VALID = "valid"
    INVALID = "invalid"


class Intent(str, Enum):
    """User intent types for classification."""
    CANCELLATION = "CANCELLATION"
    CONFIRMATION = "CONFIRMATION"
    UPDATE = "UPDATE"
    DECLINE = "DECLINE"
    SUBMISSION = "SUBMISSION"
    NONE = "NONE"


# Context keys for session.context dictionary
class ContextKey:
    """Constants for session context dictionary keys."""
    DIRECTIVE_OVERRIDE_REPLACE_MODE = "_directive_override_replace_mode"
    DIRECTIVE_OVERRIDE_APPEND_MODE = "_directive_override_append_mode"
