"""Validation enums and constants for interview action."""

from enum import Enum


class InterviewState(str, Enum):
    """Interview session state machine states."""
    IDLE = "idle"
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ValidationStatus(str, Enum):
    """Three-tier validation status for question responses."""
    VALID = "valid"
    VALID_WITH_FLAG = "valid_with_flag"
    INVALID = "invalid"

