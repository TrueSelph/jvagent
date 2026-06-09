"""Builtin utterance candidate extraction for validation grounding."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from .interview_loader import FieldDef, ValidatorDef

logger = logging.getLogger(__name__)

_EMAIL_RE = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


def _looks_like_email_validator(vdef: ValidatorDef) -> bool:
    name = (vdef.name or "").lower()
    return name == "email" or "email" in name


def _extract_builtin_candidates(
    vdef: ValidatorDef,
    user_message: str,
    kwargs: Dict[str, Any],
) -> List[str]:
    msg = (user_message or "").strip()
    if not msg:
        return []

    candidates: List[str] = []
    name = vdef.name or ""

    if name == "name" or name == "text":
        pass

    elif name == "email" or _looks_like_email_validator(vdef):
        candidates.extend(re.findall(_EMAIL_RE, msg, re.IGNORECASE))

    elif name == "phone":
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

    elif name in ("date", "date_past", "date_future"):
        for match in re.finditer(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", msg):
            candidates.append(match.group().replace("/", "-"))

    elif name == "number":
        for match in re.finditer(r"-?\d+(?:\.\d+)?", msg):
            candidates.append(match.group())

    pattern = kwargs.get("extract_pattern")
    if pattern:
        try:
            candidates.extend(re.findall(str(pattern), msg, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid extract_pattern for validator %s", name)

    return candidates


def extract_candidates_for_field(
    field: FieldDef,
    vdef: ValidatorDef,
    user_message: str,
    kwargs: Dict[str, Any],
    *,
    session: Any = None,
) -> List[str]:
    """Return ordered unique candidate substrings from user_message for a field."""
    msg = (user_message or "").strip()
    if not msg:
        return []
    return _dedupe(_extract_builtin_candidates(vdef, msg, kwargs))


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


# Back-compat alias
extract_candidates_for_question = extract_candidates_for_field
