"""Extract field value candidates from a user message for init-time seeding."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .interview_loader import QuestionDef, ValidatorDef

_EMAIL_RE = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


def _looks_like_email_validator(vdef: ValidatorDef) -> bool:
    return "email" in (vdef.name or "").lower()


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

    if vdef.name == "email" or _looks_like_email_validator(vdef):
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
