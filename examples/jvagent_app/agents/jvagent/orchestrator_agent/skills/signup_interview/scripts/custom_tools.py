"""Custom tools for the signup_interview skill (jvagent training registration).

Ported from SignupInterviewInteractAction — validators, training-slot matching,
review display, and completion handling for the skills-v2 InterviewAction path.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jvagent.action.interview_action.core.responses import (
    interview_tool_response,
    tell_user_directive,
    tell_user_with_followup_directive,
)

logger = logging.getLogger(__name__)

_SKILL_NAME = "signup_interview"

AVAILABLE_TRAINING_TIMES: List[str] = [
    "Monday 9:00 AM - 11:00 AM",
    "Monday 2:00 PM - 4:00 PM",
    "Wednesday 9:00 AM - 11:00 AM",
    "Wednesday 2:00 PM - 4:00 PM",
    "Friday 10:00 AM - 12:00 PM",
    "Saturday 9:00 AM - 12:00 PM",
]

_INVALID_TEST_DOMAINS = frozenset({"example.com", "test.com", "invalid.com"})

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


# ─── Message evaluation extractors ───────────────────────────────────


def extract_full_name_candidates(user_message: str, **kwargs: Any) -> List[str]:
    """Surface intro-style name phrases from the user's latest message."""
    msg = (user_message or "").strip()
    candidates: List[str] = []
    for pattern in _NAME_INTRO_PATTERNS:
        for match in pattern.finditer(msg):
            name = (match.group(1) or "").strip().strip(".,;")
            if name and name not in candidates:
                candidates.append(name)
    return candidates


def extract_available_times_candidates(user_message: str, **kwargs: Any) -> List[str]:
    """Surface day/time phrases that validate_available_times may match."""
    msg = (user_message or "").strip()
    if not msg:
        return []
    candidates: List[str] = []
    lower = msg.lower()
    day_names = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    for day in day_names:
        if day not in lower:
            continue
        for match in re.finditer(rf"\b{day}\b[^.;]{{0,50}}", msg, re.IGNORECASE):
            chunk = match.group().strip().strip(".,;")
            if chunk and chunk not in candidates:
                candidates.append(chunk)
        for match in re.finditer(
            rf"\b{day}\s+(?:at\s+)?\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm)?\b",
            msg,
            re.IGNORECASE,
        ):
            chunk = match.group().strip()
            if chunk not in candidates:
                candidates.append(chunk)
    return candidates


def _validation_result(
    valid: bool,
    value: str,
    validator: str,
    error: str = "",
) -> str:
    payload: Dict[str, Any] = {
        "valid": valid,
        "value": value,
        "validator": validator,
    }
    if not valid:
        payload["error"] = error
    return json.dumps(payload)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _match_training_slot(raw_input: str) -> Optional[str]:
    """Return canonical slot string if raw_input matches an available time."""
    if not raw_input:
        return None

    normalized_value = _normalize_spaces(raw_input)

    for slot in AVAILABLE_TRAINING_TIMES:
        if normalized_value == _normalize_spaces(slot):
            return slot

    input_day = None
    for day in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        if day in normalized_value:
            input_day = day
            break

    if not input_day:
        return None

    time_pattern = r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
    time_matches = re.findall(time_pattern, normalized_value)
    if not time_matches:
        return None

    input_hour = int(time_matches[0][0])
    input_period = time_matches[0][2] or None

    for slot in AVAILABLE_TRAINING_TIMES:
        normalized_slot = _normalize_spaces(slot)
        if input_day not in normalized_slot:
            continue

        slot_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", normalized_slot)
        if not slot_match:
            continue

        slot_hour = int(slot_match.group(1))
        slot_period = slot_match.group(3)
        hour_matches = input_hour == slot_hour
        period_matches = input_period is None or input_period == slot_period

        if hour_matches and period_matches:
            return slot

    # Fuzzy partial match (legacy input_handler behaviour)
    for slot in AVAILABLE_TRAINING_TIMES:
        normalized_slot = _normalize_spaces(slot)
        day_match = any(
            d in normalized_value and d in normalized_slot
            for d in ("monday", "wednesday", "friday", "saturday")
        )
        if not day_match:
            continue

        slot_time = re.search(
            r"(\d+):(\d+)\s*(am|pm)\s*-\s*(\d+):(\d+)\s*(am|pm)", normalized_slot
        )
        if not slot_time:
            continue

        start_hour = int(slot_time.group(1))
        hour_patterns = [
            rf"\b{start_hour}\b",
            rf"at\s+{start_hour}\b",
            rf"{start_hour}\s*(am|pm)\b",
            rf"{start_hour}:00",
            rf"{start_hour}:00\s*(am|pm)",
        ]
        if any(re.search(p, normalized_value, re.IGNORECASE) for p in hour_patterns):
            return slot

    return None


async def _get_conversation(visitor: Any) -> Any:
    if visitor is None:
        return None
    if hasattr(visitor, "conversation") and visitor.conversation is not None:
        return visitor.conversation
    interaction = getattr(visitor, "interaction", None)
    if interaction is not None and hasattr(interaction, "get_conversation"):
        try:
            return await interaction.get_conversation()
        except Exception:
            pass
    return None


# ─── Validators ──────────────────────────────────────────────────────


async def validate_full_name(value: str, **kwargs) -> str:
    if not value or not isinstance(value, str):
        return _validation_result(
            False, value or "", "validate_full_name", "Ask: Please provide your full name"
        )

    name = value.strip()
    if len(name) < 3:
        return _validation_result(
            False,
            value,
            "validate_full_name",
            "Ask: Please provide your complete full name",
        )

    parts = name.split()
    if len(parts) < 2:
        return _validation_result(
            False,
            value,
            "validate_full_name",
            "Ask: Please provide both your first and last name",
        )

    for part in parts:
        if len(part) < 2:
            return _validation_result(
                False,
                value,
                "validate_full_name",
                "Tell the user: Each name part should be at least 2 characters long",
            )

    if not re.match(r"^[a-zA-Z\s\-']+$", name):
        return _validation_result(
            False,
            value,
            "validate_full_name",
            "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes",
        )

    return _validation_result(True, name, "validate_full_name")


async def validate_available_times(
    value: str,
    session: Any = None,
    **kwargs,
) -> str:
    if not value or not isinstance(value, str):
        return _validation_result(
            False,
            value or "",
            "validate_available_times",
            "Ask: Please provide your available training times",
        )

    matched = _match_training_slot(value)
    if matched:
        if session is not None:
            if not isinstance(getattr(session, "context", None), dict):
                session.context = {}
            session.context["matched_training_times"] = [matched]
        return _validation_result(True, matched, "validate_available_times")

    if session is not None:
        ctx = getattr(session, "context", None)
        if isinstance(ctx, dict):
            ctx.pop("matched_training_times", None)

    available_list = ", ".join(AVAILABLE_TRAINING_TIMES)
    return _validation_result(
        False,
        value.strip(),
        "validate_available_times",
        f"Tell the user that their choice is not available and advise them to select from the available training times: {available_list}",
    )


async def validate_signup_email(value: str, **kwargs) -> str:
    if not value or not isinstance(value, str):
        return _validation_result(
            False,
            value or "",
            "validate_signup_email",
            "Ask: Please provide a valid email address",
        )

    email = value.strip().lower()
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        return _validation_result(
            False,
            value,
            "validate_signup_email",
            "Tell the user: Please provide a valid email address format (e.g., name@example.com)",
        )

    domain = email.split("@")[1] if "@" in email else ""
    if domain in _INVALID_TEST_DOMAINS:
        return _validation_result(
            False,
            value,
            "validate_signup_email",
            "Tell the user: Please provide a real email address, not a test domain",
        )

    if len(domain.split(".")) < 2:
        return _validation_result(
            False,
            value,
            "validate_signup_email",
            "Tell the user: Email domain appears to be invalid",
        )

    return _validation_result(True, email, "validate_signup_email")


# ─── Pre-tools ───────────────────────────────────────────────────────


async def get_available_training_times(
    session: Any = None,
    visitor: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    slots_text = "\n".join(f"- {slot}" for slot in AVAILABLE_TRAINING_TIMES)
    return {
        "ok": True,
        "available_times": AVAILABLE_TRAINING_TIMES,
        "timezone": "America/New_York",
        "directive": tell_user_directive(
            "What times are you available to train?",
            note=(
                "Present these available slots (Eastern Time) and ask the user to pick one:\n"
                f"{slots_text}"
            ),
        ),
    }


# ─── Post-tools ──────────────────────────────────────────────────────


def _question_text(config: Any, field_name: str, default: str) -> str:
    if config is None or not hasattr(config, "get_question"):
        return default
    q = config.get_question(field_name)
    if q is None:
        return default
    return (getattr(q, "question", None) or default).strip()


async def append_work_email_note(
    session: Any = None,
    visitor: Any = None,
    config: Any = None,
    **kwargs,
) -> str:
    email = ""
    if session is not None:
        email = (session.get_value("user_email") or "").lower()

    if "@mail.com" not in email:
        return interview_tool_response(ok=True, status="ok")

    phone_q = _question_text(
        config,
        "phone_number",
        "What is your phone number? (optional)",
    )
    return interview_tool_response(
        ok=True,
        status="ok",
        present_field="phone_number",
        response_directive=tell_user_with_followup_directive(
            "Thank you for using your work email! We'll send you special updates about jvagent training.",
            phone_q,
        ),
    )


# ─── Review handler ──────────────────────────────────────────────────


async def signup_review(
    session: Any = None,
    extracted_values: Optional[Dict[str, str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": (
            "Here's a summary of your jvagent training signup. "
            "Please confirm these details are correct before I finalize your registration."
        ),
    }

    data = extracted_values or (
        session.get_collected_summary() if session is not None else {}
    )
    if not data:
        return result

    for field_name, value in data.items():
        if field_name == "phone_number":
            if value is None or value == "" or (
                isinstance(value, str) and value.strip().lower() in ("n/a", "na")
            ):
                result["modified_values"]["phone_number"] = "__omit__"
            elif session is not None and session.is_skipped("phone_number"):
                result["modified_values"]["phone_number"] = "__omit__"
                result["additional_data"]["Phone"] = "skipped"

    return result


# ─── Completion handler ──────────────────────────────────────────────


async def signup_complete(
    session: Any = None,
    visitor: Any = None,
    interview_action: Any = None,
    extracted_values: Optional[Dict[str, str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    values = extracted_values or {}
    user_name = (values.get("user_name") or "").strip()
    user_email = (values.get("user_email") or "").strip()
    available_times = (values.get("available_times") or "").strip()
    phone_number = (values.get("phone_number") or "").strip()

    if not user_name or not user_email or not available_times:
        return {
            "directive": (
                "Some required signup fields are missing. "
                "Please go back and collect all required information."
            )
        }

    matched_times: List[str] = []
    if session is not None and isinstance(getattr(session, "context", None), dict):
        raw = session.context.get("matched_training_times") or []
        if isinstance(raw, list):
            matched_times = [str(x) for x in raw if x]

    logger.info(
        "Signup interview completed: %s (%s) — slot: %s",
        user_name,
        user_email,
        available_times,
    )

    times_note = (
        f"Your preferred times were: {', '.join(matched_times)}."
        if matched_times
        else f"Your availability: {available_times}."
    )
    return {
        "directive": (
            f"Thank you, {user_name}! Your signup for jvagent training is complete. "
            f"We will contact you at {user_email}. {times_note}"
        )
    }
