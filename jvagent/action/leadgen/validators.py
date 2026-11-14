"""Built-in field validators for LeadGenAction."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

ValidatorFn = Callable[..., Tuple[Optional[str], Optional[str]]]


def _result(
    normalized: Optional[str], error: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    return normalized, error


def validate_email(raw: str, **_: Any) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not raw.strip():
        return _result(None, "Email address cannot be empty.")
    cleaned = raw.strip()
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


def validate_person_name(
    raw: str,
    *,
    min_length: int = 2,
    max_words: int = 4,
    **_: Any,
) -> Tuple[Optional[str], Optional[str]]:
    if not raw or not str(raw).strip():
        return _result(None, "Name cannot be empty.")
    name = str(raw).strip()
    try:
        min_len = int(min_length)
    except (TypeError, ValueError):
        min_len = 2
    try:
        max_w = int(max_words)
    except (TypeError, ValueError):
        max_w = 4
    if len(name) < min_len:
        return _result(None, "Name is too short.")
    if max_w > 0 and len(name.split()) > max_w:
        return _result(None, "Name has too many words.")
    return _result(name, None)


def validate_enum(
    raw: str, *, choices: Optional[List[Any]] = None, **_: Any
) -> Tuple[Optional[str], Optional[str]]:
    if not choices:
        return _result(None, "enum validator requires choices.")
    cleaned = str(raw or "").strip()
    if not cleaned:
        return _result(None, "Value cannot be empty.")
    lookup = {
        str(c).strip().casefold(): str(c).strip() for c in choices if str(c).strip()
    }
    hit = lookup.get(cleaned.casefold())
    if hit is None:
        return _result(
            None, f"must be one of: {', '.join(str(c).strip() for c in choices)}"
        )
    return _result(hit, None)


def validate_regex(
    raw: str,
    *,
    pattern: str = "",
    flags: str = "",
    **_: Any,
) -> Tuple[Optional[str], Optional[str]]:
    if not pattern:
        return _result(None, "regex validator requires pattern.")
    cleaned = str(raw or "").strip()
    if not cleaned:
        return _result(None, "Value cannot be empty.")
    re_flags = 0
    for ch in str(flags or ""):
        if ch == "i":
            re_flags |= re.IGNORECASE
        elif ch == "m":
            re_flags |= re.MULTILINE
        elif ch == "s":
            re_flags |= re.DOTALL
    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as exc:
        return _result(None, f"invalid regex pattern: {exc}")
    if not compiled.fullmatch(cleaned):
        return _result(None, f"does not match required pattern.")
    return _result(cleaned, None)


def validate_number(
    raw: str,
    *,
    min: Optional[float] = None,  # noqa: A002 — matches validator_args key
    max: Optional[float] = None,  # noqa: A002
    **_: Any,
) -> Tuple[Optional[str], Optional[str]]:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return _result(None, "Value cannot be empty.")
    try:
        number = float(cleaned.replace(",", ""))
    except ValueError:
        return _result(None, f"'{cleaned}' is not a number.")
    if min is not None and number < float(min):
        return _result(None, f"must be at least {min}.")
    if max is not None and number > float(max):
        return _result(None, f"must be at most {max}.")
    if number.is_integer():
        return _result(str(int(number)), None)
    return _result(str(number), None)


_DEFAULT_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
)


def validate_date(
    raw: str,
    *,
    formats: Optional[List[str]] = None,
    **_: Any,
) -> Tuple[Optional[str], Optional[str]]:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return _result(None, "Value cannot be empty.")
    # Accept plain ISO date without going through strptime edge cases.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            datetime.strptime(cleaned, "%Y-%m-%d")
            return _result(cleaned, None)
        except ValueError:
            return _result(None, f"'{cleaned}' is not a valid date.")
    tried = list(formats) if formats else list(_DEFAULT_DATE_FORMATS)
    for fmt in tried:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return _result(parsed.strftime("%Y-%m-%d"), None)
        except ValueError:
            continue
    return _result(None, f"'{cleaned}' is not a recognised date.")


_BUILTIN: Dict[str, ValidatorFn] = {
    "email": validate_email,
    "phone": validate_phone_e164,
    "phone_e164": validate_phone_e164,
    "phone_gy": validate_phone_gy,
    "person_name": validate_person_name,
    "name": validate_person_name,
    "enum": validate_enum,
    "regex": validate_regex,
    "number": validate_number,
    "date": validate_date,
}


def known_validators() -> FrozenSet[str]:
    return frozenset(_BUILTIN)


def locale_validator(locale: str) -> str:
    """Resolve a ``phone_locale`` to a registered ``phone_<locale>`` validator.

    Data-driven: any built-in named ``phone_<locale>`` works, so a deployment can
    add a locale validator without touching the engine. Unknown or empty locale
    returns ``""``; the caller falls back to E164.
    """
    key = str(locale or "").strip().lower()
    if not key:
        return ""
    name = f"phone_{key}"
    return name if name in _BUILTIN else ""


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
        return _result(None, f"unknown validator '{name}'.")
    return fn(str(value), **(validator_args or {}))
