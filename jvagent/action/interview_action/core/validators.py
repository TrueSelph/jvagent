"""Built-in format validators for InterviewAction.

Ported from skill_interview_action/validators.py to avoid cross-action imports.
Self-contained — defines ExtractionStatus locally so no relative imports are
needed at module level (relative imports break under jvagent's dynamic loading).

Each validator returns a tuple of (ExtractionStatus, Optional[error_message], Optional[autocorrected_value]).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Optional, Tuple


class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    INVALID = "invalid"


ValidationResult = Tuple[ExtractionStatus, Optional[str], Optional[str]]


def validate_phone(value: str, **kwargs) -> ValidationResult:
    length = kwargs.get("exact_length", kwargs.get("length"))
    min_len = kwargs.get("min_length", 1)
    max_len = kwargs.get("max_length")
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ExtractionStatus.INVALID, "Ask: Please provide a phone number", None
    if length and len(digits) != length:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a {length}-digit phone number",
            None,
        )
    if len(digits) < min_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a phone number with at least {min_len} digits",
            None,
        )
    if max_len and len(digits) > max_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a phone number with at most {max_len} digits",
            None,
        )
    default_pattern = r"^\d{10}$"
    pattern = kwargs.get("pattern", default_pattern)
    if not length and not max_len and min_len <= 1:
        if not re.match(pattern, digits):
            return (
                ExtractionStatus.INVALID,
                "Ask: Please provide a valid 10-digit phone number",
                None,
            )
    return ExtractionStatus.EXTRACTED, None, digits


def validate_email(value: str, **kwargs) -> ValidationResult:
    if not value or not isinstance(value, str):
        return (
            ExtractionStatus.INVALID,
            "Ask: Please provide a valid email address",
            None,
        )
    email = value.strip().lower()
    default_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    pattern = kwargs.get("pattern", default_pattern)
    if not re.match(pattern, email):
        return (
            ExtractionStatus.INVALID,
            "Ask: Please provide a valid email address (e.g. name@example.com)",
            None,
        )
    min_len = kwargs.get("min_length")
    max_len = kwargs.get("max_length")
    if min_len and len(email) < min_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Email must be at least {min_len} characters",
            None,
        )
    if max_len and len(email) > max_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Email must be at most {max_len} characters",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, email


def validate_name(value: str, **kwargs) -> ValidationResult:
    if not value or not isinstance(value, str):
        return ExtractionStatus.INVALID, "Ask: Please provide your full name", None
    name = value.strip()
    parts = name.split()
    if len(parts) < 2:
        return (
            ExtractionStatus.INVALID,
            "Ask: Please provide both your first and last name",
            None,
        )
    for part in parts:
        if len(part) < 2:
            return (
                ExtractionStatus.INVALID,
                "Ask: Each name part should be at least 2 characters",
                None,
            )
    default_pattern = r"^[a-zA-Z\s\-'.]+$"
    pattern = kwargs.get("pattern", default_pattern)
    if not re.match(pattern, name):
        return (
            ExtractionStatus.INVALID,
            "Ask: Name should only contain letters, spaces, hyphens, apostrophes, and periods",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, name


def validate_number(value: str, min_length: int = 1, **kwargs) -> ValidationResult:
    if not value or not isinstance(value, str):
        return ExtractionStatus.INVALID, "Ask: Please provide a valid number", None
    digits = value.strip()
    max_len = kwargs.get("max_length")
    exact_len = kwargs.get("exact_length", kwargs.get("length"))
    allow_negative = kwargs.get("allow_negative", False)
    allow_decimal = kwargs.get("allow_decimal", False)

    if allow_decimal:
        cleaned = digits.replace("-", "").replace(".", "")
        if not cleaned.isdigit():
            return ExtractionStatus.INVALID, "Ask: Please provide a valid number", None
    else:
        cleaned = digits.replace("-", "") if allow_negative else digits
        if not cleaned.isdigit():
            return (
                ExtractionStatus.INVALID,
                "Ask: Please provide a valid number (digits only)",
                None,
            )

    min_len = kwargs.get("min_length", min_length)
    if exact_len and len(cleaned) != exact_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: The number should be exactly {exact_len} digits long",
            None,
        )
    if len(cleaned) < min_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: The number should be at least {min_len} digits long",
            None,
        )
    if max_len and len(cleaned) > max_len:
        return (
            ExtractionStatus.INVALID,
            f"Ask: The number should be at most {max_len} digits long",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, digits


def validate_date_past(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> ValidationResult:
    if not value or not isinstance(value, str):
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a valid date in {date_format} format",
            None,
        )
    v = value.strip()
    try:
        parsed = datetime.strptime(v, date_format).date()
    except ValueError:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a valid date in {date_format} format",
            None,
        )
    if parsed >= date.today():
        return ExtractionStatus.INVALID, "Ask: Please provide a date in the past", None
    return ExtractionStatus.EXTRACTED, None, v


def validate_date(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> ValidationResult:
    if not value or not isinstance(value, str):
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a valid date in {date_format} format",
            None,
        )
    v = value.strip()
    try:
        datetime.strptime(v, date_format).date()
    except ValueError:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a valid date in {date_format} format",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, v


def validate_date_future(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> ValidationResult:
    status, err, autocorrected = validate_date(value, date_format=date_format)
    if status != ExtractionStatus.EXTRACTED:
        return status, err, autocorrected
    try:
        parsed = datetime.strptime(value.strip(), date_format).date()
    except ValueError:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a valid future date in {date_format} format",
            None,
        )
    if parsed <= date.today():
        return ExtractionStatus.INVALID, "Ask: Please provide a future date", None
    return ExtractionStatus.EXTRACTED, None, value.strip()


def validate_yes_no(value: str, **kwargs) -> ValidationResult:
    if not value or not isinstance(value, str):
        return ExtractionStatus.INVALID, "Ask: Please answer yes or no", None
    normalized = value.strip().lower()
    if normalized in ("yes", "yeah", "yep", "y", "sure", "ok", "okay"):
        return ExtractionStatus.EXTRACTED, None, "yes"
    if normalized in ("no", "nope", "n", "nah", "never"):
        return ExtractionStatus.EXTRACTED, None, "no"
    return ExtractionStatus.INVALID, "Ask: Please answer yes or no", None


def validate_text(value: str, min_length: int = 2, **kwargs) -> ValidationResult:
    max_length = kwargs.get("max_length")
    exact_length = kwargs.get("exact_length", kwargs.get("length"))
    pattern = kwargs.get("pattern")
    if not value or not isinstance(value, str):
        return ExtractionStatus.INVALID, "Ask: Please provide a response", None
    v = value.strip()
    if exact_length and len(v) != exact_length:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a response that is exactly {exact_length} characters long",
            None,
        )
    if len(v) < min_length:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a more detailed response (at least {min_length} characters)",
            None,
        )
    if max_length and len(v) > max_length:
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please provide a shorter response (at most {max_length} characters)",
            None,
        )
    if pattern and not re.match(pattern, v):
        return (
            ExtractionStatus.INVALID,
            "Ask: Please provide a valid response matching the expected format",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, v


def validate_address(value: str, min_length: int = 5, **kwargs) -> ValidationResult:
    return validate_text(value, min_length=min_length, **kwargs)


def validate_description(
    value: str, min_length: int = 10, **kwargs
) -> ValidationResult:
    return validate_text(value, min_length=min_length, **kwargs)


def validate_list(
    value: str, allowed_items: Optional[list] = None, **kwargs
) -> ValidationResult:
    if not value or not isinstance(value, str):
        return ExtractionStatus.INVALID, "Ask: Please select an option", None
    v = value.strip()
    items = allowed_items or kwargs.get("allowed_values")
    if items:
        normalized = v.lower()
        for item in items:
            if normalized == item.lower():
                return ExtractionStatus.EXTRACTED, None, item
        items_str = ", ".join(str(i) for i in items)
        return (
            ExtractionStatus.INVALID,
            f"Ask: Please select one of the following: {items_str}",
            None,
        )
    return ExtractionStatus.EXTRACTED, None, v


BUILTIN_VALIDATORS = {
    "phone": validate_phone,
    "email": validate_email,
    "name": validate_name,
    "number": validate_number,
    "date": validate_date,
    "date_past": validate_date_past,
    "date_future": validate_date_future,
    "yes_no": validate_yes_no,
    "text": validate_text,
    "address": validate_address,
    "description": validate_description,
    "list": validate_list,
}


def get_validator(format_name: str):
    return BUILTIN_VALIDATORS.get(format_name)
