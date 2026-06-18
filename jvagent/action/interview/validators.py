"""Built-in format validators for InterviewAction.

Each validator returns ``{"valid": bool, "value"?: str, "error"?: str}``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

ValidatorFn = Callable[..., Dict[str, Any]]


def _ok(value: str) -> Dict[str, Any]:
    return {"valid": True, "value": value}


def _fail(error: str) -> Dict[str, Any]:
    return {"valid": False, "error": error}


def validate_phone(value: str, **kwargs) -> Dict[str, Any]:
    length = kwargs.get("exact_length", kwargs.get("length"))
    min_len = kwargs.get("min_length", 1)
    max_len = kwargs.get("max_length")
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return _fail("Ask: Please provide a phone number")
    # ``country_code``: prepend it to a bare LOCAL number — one whose length is
    # exactly (full length − country-code length), e.g. a 7-digit number with
    # country_code=592 → the 10-digit full number. Any other length is left as-is,
    # so the length/pattern checks below accept ONLY a bare local number (after the
    # code is added) or an already-full number — nothing in between.
    country_code = re.sub(r"\D", "", str(kwargs.get("country_code") or ""))
    if country_code and not digits.startswith(country_code):
        target_full = length or max_len or 10
        if len(digits) == target_full - len(country_code):
            digits = country_code + digits
    if length and len(digits) != length:
        return _fail(f"Ask: Please provide a {length}-digit phone number")
    if len(digits) < min_len:
        return _fail(
            f"Ask: Please provide a phone number with at least {min_len} digits"
        )
    if max_len and len(digits) > max_len:
        return _fail(
            f"Ask: Please provide a phone number with at most {max_len} digits"
        )
    default_pattern = r"^\d{10}$"
    pattern = kwargs.get("pattern", default_pattern)
    if not length and not max_len and min_len <= 1:
        if not re.match(pattern, digits):
            return _fail("Ask: Please provide a valid 10-digit phone number")
    return _ok(digits)


def validate_email(value: str, **kwargs) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail("Ask: Please provide a valid email address")
    email = value.strip().lower()
    default_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    pattern = kwargs.get("pattern", default_pattern)
    if not re.match(pattern, email):
        return _fail(
            "Ask: Please provide a valid email address (e.g. name@example.com)"
        )
    min_len = kwargs.get("min_length")
    max_len = kwargs.get("max_length")
    if min_len and len(email) < min_len:
        return _fail(f"Ask: Email must be at least {min_len} characters")
    if max_len and len(email) > max_len:
        return _fail(f"Ask: Email must be at most {max_len} characters")
    return _ok(email)


def validate_name(value: str, **kwargs) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail("Ask: Please provide your full name")
    name = value.strip()
    parts = name.split()
    if len(parts) < 2:
        return _fail("Ask: Please provide both your first and last name")
    for part in parts:
        if len(part) < 2:
            return _fail("Ask: Each name part should be at least 2 characters")
    default_pattern = r"^[a-zA-Z\s\-'.]+$"
    pattern = kwargs.get("pattern", default_pattern)
    if not re.match(pattern, name):
        return _fail(
            "Ask: Name should only contain letters, spaces, hyphens, apostrophes, and periods"
        )
    return _ok(name)


def validate_number(value: str, min_length: int = 1, **kwargs) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail("Ask: Please provide a valid number")
    digits = value.strip()
    max_len = kwargs.get("max_length")
    exact_len = kwargs.get("exact_length", kwargs.get("length"))
    allow_negative = kwargs.get("allow_negative", False)
    allow_decimal = kwargs.get("allow_decimal", False)

    if allow_decimal:
        cleaned = digits.replace("-", "").replace(".", "")
        if not cleaned.isdigit():
            return _fail("Ask: Please provide a valid number")
    else:
        cleaned = digits.replace("-", "") if allow_negative else digits
        if not cleaned.isdigit():
            return _fail("Ask: Please provide a valid number (digits only)")

    min_len = kwargs.get("min_length", min_length)
    if exact_len and len(cleaned) != exact_len:
        return _fail(f"Ask: The number should be exactly {exact_len} digits long")
    if len(cleaned) < min_len:
        return _fail(f"Ask: The number should be at least {min_len} digits long")
    if max_len and len(cleaned) > max_len:
        return _fail(f"Ask: The number should be at most {max_len} digits long")
    return _ok(digits)


def validate_date_past(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail(f"Ask: Please provide a valid date in {date_format} format")
    v = value.strip()
    try:
        parsed = datetime.strptime(v, date_format).date()
    except ValueError:
        return _fail(f"Ask: Please provide a valid date in {date_format} format")
    if parsed >= date.today():
        return _fail("Ask: Please provide a date in the past")
    return _ok(v)


def validate_date(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail(f"Ask: Please provide a valid date in {date_format} format")
    v = value.strip()
    try:
        datetime.strptime(v, date_format).date()
    except ValueError:
        return _fail(f"Ask: Please provide a valid date in {date_format} format")
    return _ok(v)


def validate_date_future(
    value: str, date_format: str = "%d-%m-%Y", **kwargs
) -> Dict[str, Any]:
    result = validate_date(value, date_format=date_format, **kwargs)
    if not result.get("valid"):
        return result
    try:
        parsed = datetime.strptime(value.strip(), date_format).date()
    except ValueError:
        return _fail(f"Ask: Please provide a valid future date in {date_format} format")
    if parsed <= date.today():
        return _fail("Ask: Please provide a future date")
    return _ok(value.strip())


def validate_yes_no(value: str, **kwargs) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail("Ask: Please answer yes or no")
    normalized = value.strip().lower()
    if normalized in ("yes", "yeah", "yep", "y", "sure", "ok", "okay"):
        return _ok("yes")
    if normalized in ("no", "nope", "n", "nah", "never"):
        return _ok("no")
    return _fail("Ask: Please answer yes or no")


def validate_text(value: str, min_length: int = 2, **kwargs) -> Dict[str, Any]:
    max_length = kwargs.get("max_length")
    exact_length = kwargs.get("exact_length", kwargs.get("length"))
    pattern = kwargs.get("pattern")
    if not value or not isinstance(value, str):
        return _fail("Ask: Please provide a response")
    v = value.strip()
    if exact_length and len(v) != exact_length:
        return _fail(
            f"Ask: Please provide a response that is exactly {exact_length} characters long"
        )
    if len(v) < min_length:
        return _fail(
            f"Ask: Please provide a more detailed response (at least {min_length} characters)"
        )
    if max_length and len(v) > max_length:
        return _fail(
            f"Ask: Please provide a shorter response (at most {max_length} characters)"
        )
    if pattern and not re.match(pattern, v):
        return _fail(
            "Ask: Please provide a valid response matching the expected format"
        )
    return _ok(v)


def validate_address(value: str, min_length: int = 5, **kwargs) -> Dict[str, Any]:
    return validate_text(value, min_length=min_length, **kwargs)


def validate_description(value: str, min_length: int = 10, **kwargs) -> Dict[str, Any]:
    return validate_text(value, min_length=min_length, **kwargs)


def validate_list(
    value: str, allowed_items: Optional[list] = None, **kwargs
) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return _fail("Ask: Please select an option")
    v = value.strip()
    items = allowed_items or kwargs.get("allowed_values")
    if items:
        normalized = v.lower()
        for item in items:
            if normalized == item.lower():
                return _ok(item)
        items_str = ", ".join(str(i) for i in items)
        return _fail(f"Ask: Please select one of the following: {items_str}")
    return _ok(v)


BUILTIN_VALIDATORS: Dict[str, ValidatorFn] = {
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


def get_validator(format_name: str) -> Optional[ValidatorFn]:
    return BUILTIN_VALIDATORS.get(format_name)
