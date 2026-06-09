"""Entity candidate extraction for per-message evaluation (not auto-store)."""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from .interview_loader import InterviewSpec, QuestionDef, ValidatorDef

logger = logging.getLogger(__name__)

_EMAIL_RE = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


def _looks_like_email_validator(vdef: ValidatorDef) -> bool:
    name = (vdef.name or "").lower()
    return name == "email" or "email" in name


def _call_custom_extractor(
    func: Callable[..., Any],
    user_message: str,
    kwargs: Dict[str, Any],
    *,
    session: Any = None,
) -> List[str]:
    params = set(inspect.signature(func).parameters.keys())
    call_kwargs = {k: v for k, v in kwargs.items() if k in params}
    if "user_message" in params:
        call_kwargs["user_message"] = user_message
    elif "message" in params:
        call_kwargs["message"] = user_message
    if session is not None and "session" in params:
        call_kwargs["session"] = session
    try:
        result = func(**call_kwargs)
    except TypeError:
        result = func(user_message)
    if not isinstance(result, list):
        return []
    return [str(item).strip() for item in result if str(item).strip()]


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
        # Short direct answers handled via CTX_QUESTION_PRESENTED in message_evaluation.
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


def extract_candidates_for_question(
    question: QuestionDef,
    vdef: ValidatorDef,
    user_message: str,
    kwargs: Dict[str, Any],
    *,
    spec: Optional[InterviewSpec] = None,
    load_fn: Optional[Callable[[str], Any]] = None,
    session: Any = None,
) -> List[str]:
    """Return ordered unique candidate substrings from user_message for a question."""
    msg = (user_message or "").strip()
    if not msg:
        return []

    candidates: List[str] = []

    if spec and load_fn:
        edef = spec.get_extractor(vdef.name)
        if edef and edef.function:
            func = load_fn(edef.function)
            if func:
                candidates.extend(
                    _call_custom_extractor(func, msg, kwargs, session=session)
                )
                return _dedupe(candidates)

    candidates.extend(_extract_builtin_candidates(vdef, msg, kwargs))
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
