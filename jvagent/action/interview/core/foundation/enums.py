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
# Import from utils for centralized constants
from ..utils.constants import (
    CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE,
    CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE,
)

class ContextKey:
    """Constants for session context dictionary keys.
    
    Note: Values are imported from utils.constants for centralized management.
    This class is maintained for backward compatibility.
    """
    DIRECTIVE_OVERRIDE_REPLACE_MODE = CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE
    DIRECTIVE_OVERRIDE_APPEND_MODE = CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE
