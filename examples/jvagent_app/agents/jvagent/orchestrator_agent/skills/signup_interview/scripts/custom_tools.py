"""Custom tools for the signup_interview skill (jvagent training registration).

Every hook takes the single ``ctx`` (HookExecutionContext): read inputs as
attributes (``ctx.value``, ``ctx.session``, ``ctx.extracted_values``), furnish
user-facing text via ``ctx.say`` and control/return data via ``ctx.tool_response``
(or ``ctx.valid`` / ``ctx.invalid`` for validators).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

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

    # Fuzzy partial match — normalize shorthand like "Saturday at 9" to a full slot
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


# ─── Validators ──────────────────────────────────────────────────────


async def validate_full_name(ctx) -> Dict[str, Any]:
    value = ctx.value
    if not value or not isinstance(value, str):
        return ctx.invalid("Please provide your full name", value=value or "")

    name = value.strip()
    if len(name) < 3:
        return ctx.invalid("Please provide your complete full name")

    parts = name.split()
    if len(parts) < 2:
        return ctx.invalid("Please provide both your first and last name")

    for part in parts:
        if len(part) < 2:
            return ctx.invalid("Each name part should be at least 2 characters long")

    if not re.match(r"^[a-zA-Z\s\-']+$", name):
        return ctx.invalid(
            "Name should only contain letters, spaces, hyphens, and apostrophes"
        )

    return ctx.valid(value=name)


async def validate_available_times(ctx) -> Dict[str, Any]:
    value = ctx.value
    if not value or not isinstance(value, str):
        return ctx.invalid(
            "Please provide your available training times", value=value or ""
        )

    session = ctx.session
    matched = _match_training_slot(value)
    if matched:
        if session is not None:
            if not isinstance(getattr(session, "context", None), dict):
                session.context = {}
            session.context["matched_training_times"] = [matched]
        return ctx.valid(value=matched)

    if session is not None:
        sctx = getattr(session, "context", None)
        if isinstance(sctx, dict):
            sctx.pop("matched_training_times", None)

    available_list = ", ".join(AVAILABLE_TRAINING_TIMES)
    return ctx.invalid(
        "That time isn't available. Please pick from the available training "
        f"times: {available_list}",
        value=value.strip(),
    )


_IN_PERSON_ALIASES = frozenset(
    {"in person", "in-person", "inperson", "onsite", "on-site", "on site", "physical"}
)
_VIRTUAL_ALIASES = frozenset(
    {"virtual", "online", "remote", "zoom", "video", "from home"}
)


async def validate_training_format(ctx) -> Dict[str, Any]:
    """Normalize Saturday-session attendance preference (branch-only field)."""
    value = ctx.value
    if not value or not isinstance(value, str):
        return ctx.invalid(
            "Will you attend in person or join virtually?", value=value or ""
        )

    normalized = _normalize_spaces(value)
    if normalized in _IN_PERSON_ALIASES or "in person" in normalized:
        return ctx.valid(value="In person")
    if (
        normalized in _VIRTUAL_ALIASES
        or "virtual" in normalized
        or "online" in normalized
    ):
        return ctx.valid(value="Virtual")

    return ctx.invalid(
        "Please say whether you will attend in person or join virtually.",
        value=value.strip(),
    )


async def validate_signup_email(ctx) -> Dict[str, Any]:
    value = ctx.value
    if not value or not isinstance(value, str):
        return ctx.invalid("Please provide a valid email address", value=value or "")

    email = value.strip().lower()
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        return ctx.invalid(
            "Please provide a valid email address format " "(e.g., name@example.com)"
        )

    domain = email.split("@")[1] if "@" in email else ""
    if domain in _INVALID_TEST_DOMAINS:
        return ctx.invalid("Please provide a real email address, not a test domain")

    if len(domain.split(".")) < 2:
        return ctx.invalid("Email domain appears to be invalid")

    return ctx.valid(value=email)


# ─── Pre-tools ───────────────────────────────────────────────────────


async def get_available_training_times(ctx) -> str:
    slots_text = "\n".join(f"- {slot}" for slot in AVAILABLE_TRAINING_TIMES)
    # Two sequential statements: the slot list the user must see, then the question.
    # ctx.say is the single user-text channel — inert off the activation run, so the
    # slot list never bleeds onto a later turn.
    ctx.say(
        [
            f"Here are the available training slots (Eastern Time) — please pick one:\n{slots_text}",
            "What times are you available to train?",
        ]
    )
    return ctx.tool_response(
        ok=True,
        status="ok",
        available_times=AVAILABLE_TRAINING_TIMES,
        timezone="America/New_York",
    )


# ─── Post-tools ──────────────────────────────────────────────────────


async def append_work_email_note(ctx) -> str:
    email = ""
    if ctx.session is not None:
        email = (ctx.session.get_value("user_email") or "").lower()

    if "@mail.com" not in email:
        return ctx.tool_response(ok=True, status="ok")

    # Return a NOTE (not say): the framework pairs it with the authoritative next
    # question computed from the FINAL settled state. A note must not bake in a
    # next-field question — it would go stale when later batch fields fill it in.
    return ctx.tool_response(
        ok=True,
        status="ok",
        note=(
            "Thank you for using your work email! We'll send you special updates "
            "about jvagent training."
        ),
    )


# ─── Review handler ──────────────────────────────────────────────────


async def signup_review(ctx) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": (
            "Here's a summary of your jvagent training signup. "
            "Please confirm these details are correct before I finalize your registration."
        ),
    }

    session = ctx.session
    data = ctx.extracted_values or (
        session.get_collected_summary() if session is not None else {}
    )
    if not data:
        return result

    for field_name, value in data.items():
        if field_name == "phone_number":
            if (
                value is None
                or value == ""
                or (isinstance(value, str) and value.strip().lower() in ("n/a", "na"))
            ):
                result["modified_values"]["phone_number"] = "__omit__"
            elif session is not None and session.is_skipped("phone_number"):
                result["modified_values"]["phone_number"] = "__omit__"

    return result


# ─── Completion handler ──────────────────────────────────────────────


async def signup_complete(ctx) -> str:
    values = ctx.extracted_values or {}
    user_name = (values.get("user_name") or "").strip()
    user_email = (values.get("user_email") or "").strip()
    available_times = (values.get("available_times") or "").strip()

    if not user_name or not user_email or not available_times:
        ctx.say(
            "Some required signup fields are missing. "
            "Please go back and collect all required information."
        )
        return ctx.tool_response(ok=True, status="ok")

    session = ctx.session
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

    training_format = (values.get("training_format") or "").strip()
    employer_name = (values.get("employer_name") or "").strip()
    times_note = (
        f"Your preferred times were: {', '.join(matched_times)}."
        if matched_times
        else f"Your availability: {available_times}."
    )
    if training_format:
        times_note = f"{times_note} Format: {training_format}."
    employer_note = f" Employer: {employer_name}." if employer_name else ""
    ctx.say(
        f"Thank you, {user_name}! Your signup for jvagent training is complete. "
        f"We will contact you at {user_email}.{employer_note} {times_note}"
    )
    return ctx.tool_response(ok=True, status="ok")
