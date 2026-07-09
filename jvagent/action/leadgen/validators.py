"""Built-in field validators for LeadGenAction."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Tuple

ValidatorFn = Callable[..., Tuple[Optional[str], Optional[str]]]


def _result(
    normalized: Optional[str], error: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    return normalized, error


def validate_email(raw: str, **_: Any) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not raw.strip():
        return _result(None, "Email address cannot be empty.")
    cleaned = raw.strip()
    if cleaned.upper() == "N/A":
        return _result(cleaned, None)
    if cleaned.count("@") != 1:
        return _result(
            None, f"Invalid email '{cleaned}': must contain exactly one '@'."
        )
    if " " in cleaned:
        return _result(None, f"Invalid email '{cleaned}': cannot contain spaces.")
    local, domain = cleaned.split("@")
    if not local or not domain or "." not in domain:
        return _result(None, f"Invalid email '{cleaned}'.")
    return _result(cleaned.lower(), None)


def validate_phone_e164(raw: str, **_: Any) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not raw.strip():
        return _result(None, "Phone number cannot be empty.")
    cleaned = re.sub(r"[^\d+]", "", raw.strip())
    digits = re.sub(r"[^\d]", "", cleaned)
    if len(digits) < 7:
        return _result(None, f"Invalid phone '{raw.strip()}': need at least 7 digits.")
    if not cleaned.startswith("+"):
        return _result(f"+{digits}", None)
    return _result(cleaned, None)


def validate_phone_gy(raw: str, **_: Any) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not raw.strip():
        return _result(None, "Phone number cannot be empty.")
    cleaned = re.sub(r"[^\d+]", "", raw.strip())
    digits = re.sub(r"[^\d]", "", cleaned)
    if len(digits) < 7:
        return _result(None, f"Invalid phone '{raw.strip()}': need at least 7 digits.")
    if len(digits) == 7:
        return _result(f"+592 {digits[:3]} {digits[3:]}", None)
    if len(digits) == 10 and digits.startswith("592"):
        return _result(f"+592 {digits[3:6]} {digits[6:]}", None)
    if not cleaned.startswith("+"):
        return _result(f"+{digits}", None)
    return _result(cleaned, None)


def validate_person_name(raw: str, **_: Any) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not str(raw).strip():
        return _result(None, "Name cannot be empty.")
    name = str(raw).strip()
    if len(name) < 2:
        return _result(None, "Name is too short.")
    return _result(name, None)


_BUILTIN: Dict[str, ValidatorFn] = {
    "email": validate_email,
    "phone": validate_phone_e164,
    "phone_e164": validate_phone_e164,
    "phone_gy": validate_phone_gy,
    "person_name": validate_person_name,
    "name": validate_person_name,
}


def get_validator(name: str) -> Optional[ValidatorFn]:
    if not name:
        return None
    return _BUILTIN.get(name.strip().lower())


def run_validator(
    name: str,
    value: Any,
    validator_args: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    fn = get_validator(name)
    if fn is None:
        text = str(value).strip() if value is not None else ""
        return _result(text or None, None if text else "Value cannot be empty.")
    return fn(str(value), **(validator_args or {}))
