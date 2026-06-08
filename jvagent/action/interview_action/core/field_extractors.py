"""Entity candidate extraction for per-message evaluation (not auto-store)."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .interview_loader import QuestionDef, ValidatorDef

_EMAIL_RE = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

_NAME_INTRO_PATTERNS = (
    re.compile(
        r"(?:my name is|i'm|i am|call me|this is)\s+"
        r"([A-Za-z][A-Za-z\s'\-]{1,60}?)"
        r"(?:\s+and\b|\s*,|\s*\.|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:hello|hi|hey)[,.]?\s+(?:my name is|i'm|i am)\s+"
        r"([A-Za-z][A-Za-z\s'\-]{1,60}?)"
        r"(?:\s+and\b|\s*,|\s*\.|$)",
        re.IGNORECASE,
    ),
)

_DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _looks_like_email_validator(vdef: ValidatorDef) -> bool:
    return "email" in (vdef.name or "").lower()


def _extract_name_candidates(msg: str) -> List[str]:
    candidates: List[str] = []
    for pattern in _NAME_INTRO_PATTERNS:
        for match in pattern.finditer(msg):
            name = (match.group(1) or "").strip().strip(".,;")
            if name and name not in candidates:
                candidates.append(name)
    return candidates


def _extract_training_time_candidates(msg: str) -> List[str]:
    """Surface day/time phrases that validate_available_times may match."""
    candidates: List[str] = []
    lower = msg.lower()
    for day in _DAY_NAMES:
        if day not in lower:
            continue
        for match in re.finditer(
            rf"\b{day}\b[^.;]{{0,50}}",
            msg,
            re.IGNORECASE,
        ):
            chunk = match.group().strip().strip(".,;")
            if chunk and chunk not in candidates:
                candidates.append(chunk)
        # compact forms: "Monday at 9", "Monday 9am"
        for match in re.finditer(
            rf"\b{day}\s+(?:at\s+)?\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm)?\b",
            msg,
            re.IGNORECASE,
        ):
            chunk = match.group().strip()
            if chunk not in candidates:
                candidates.append(chunk)
    return candidates


def extract_candidates_for_question(
    question: QuestionDef,
    vdef: ValidatorDef,
    user_message: str,
    kwargs: Dict[str, Any],
) -> List[str]:
    """Return ordered unique candidate substrings from user_message for a question."""
    msg = (user_message or "").strip()
    if not msg:
        return []

    candidates: List[str] = []

    if vdef.name == "validate_full_name":
        candidates.extend(_extract_name_candidates(msg))

    elif vdef.name == "validate_available_times":
        candidates.extend(_extract_training_time_candidates(msg))

    elif vdef.name == "email" or _looks_like_email_validator(vdef):
        candidates.extend(re.findall(_EMAIL_RE, msg, re.IGNORECASE))

    elif vdef.name == "phone":
        exact = int(kwargs.get("exact_length", kwargs.get("length", 10)))
        for match in re.finditer(r"\d{%d,}" % exact, msg):
            chunk = match.group()
            if len(chunk) == exact:
                candidates.append(chunk)
            elif len(chunk) > exact:
                candidates.append(chunk[:exact])
                candidates.append(chunk[-exact:])
        all_digits = re.sub(r"\D", "", msg)
        if len(all_digits) >= exact:
            candidates.append(all_digits[-exact:])

    elif vdef.name == "date_past":
        for match in re.finditer(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", msg):
            candidates.append(match.group().replace("/", "-"))

    elif vdef.name in (
        "validate_tracking_number",
        "validate_alternative_tracking_number",
    ):
        min_len = int(kwargs.get("min_length", 10))
        for match in re.finditer(r"\d{%d,}" % min_len, msg):
            candidates.append(match.group())
        all_digits = "".join(c for c in msg if c.isdigit())
        if len(all_digits) >= min_len:
            candidates.append(all_digits)

    elif vdef.name == "validate_id_number":
        for match in re.finditer(r"\b\d{8,9}\b", msg):
            candidates.append(match.group())
        all_digits = "".join(c for c in msg if c.isdigit())
        if 8 <= len(all_digits) <= 9:
            candidates.append(all_digits)
        for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]{5,11}\b", msg):
            candidates.append(match.group())

    elif vdef.name == "validate_invoice_value":
        for match in re.finditer(r"[\d,.]+", msg):
            cleaned = re.sub(r"[$,\s]", "", match.group())
            if cleaned and any(c.isdigit() for c in cleaned):
                candidates.append(cleaned)

    return _dedupe(candidates)


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out
