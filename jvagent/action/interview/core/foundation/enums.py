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
