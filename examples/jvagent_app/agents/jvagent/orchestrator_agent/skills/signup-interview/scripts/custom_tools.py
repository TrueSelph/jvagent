"""Custom tools for the signup-interview skill (jvagent training registration).

Every hook takes the single ``ctx`` (HookExecutionContext): read inputs as
attributes (``ctx.value``, ``ctx.session``, ``ctx.extracted_values``), furnish
user-facing text via ``ctx.say`` and control/return data via ``ctx.tool_response``
(or ``ctx.valid`` / ``ctx.invalid`` for validators).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from jvagent.action.interview.flow import compute_missing_required
from jvagent.action.interview.hooks import HookExecutionContext

_SKILL_NAME = "signup-interview"

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

    # Accept one explicit day/start time and an optional range. Never ignore
    # a conflicting period, minute, end time, negation or alternative day.
    clock = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    pattern = r"(monday|wednesday|friday|saturday)\s+(?:at\s+)?" + clock
    pattern += r"(?:\s*(?:-|to)\s*" + clock + r")?"
    match = re.fullmatch(pattern, normalized_value)
    if match is None:
        return None
    day, hour, minute, period, end_hour, end_minute, end_period = match.groups()

    def clock_matches(
        supplied_hour: str,
        supplied_minute: Optional[str],
        supplied_period: Optional[str],
        expected: str,
    ) -> bool:
        target = re.fullmatch(clock, expected)
        assert target is not None
        return (
            int(supplied_hour) == int(target[1])
            and int(supplied_minute or "0") == int(target[2] or "0")
            and (supplied_period is None or supplied_period == target[3])
        )

    candidates: List[str] = []
    for slot in AVAILABLE_TRAINING_TIMES:
        slot_day, times = _normalize_spaces(slot).split(" ", 1)
        start, end = times.split(" - ")
        if slot_day != day or not clock_matches(hour, minute, period, start):
            continue
        if end_hour is not None and not clock_matches(
            end_hour, end_minute, end_period, end
        ):
            continue
        candidates.append(slot)
    return candidates[0] if len(candidates) == 1 else None


# ─── Validators ──────────────────────────────────────────────────────


async def validate_full_name(ctx: HookExecutionContext) -> Dict[str, Any]:
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


async def validate_available_times(ctx: HookExecutionContext) -> Dict[str, Any]:
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


async def validate_training_format(ctx: HookExecutionContext) -> Dict[str, Any]:
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


async def validate_signup_email(ctx: HookExecutionContext) -> Dict[str, Any]:
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


async def get_available_training_times(ctx: HookExecutionContext) -> str:
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


async def append_work_email_note(ctx: HookExecutionContext) -> str:
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
        note=("Your work email has been recorded with these signup details."),
    )


# ─── Review handler ──────────────────────────────────────────────────


async def signup_review(ctx: HookExecutionContext) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": (
            "Here's a summary of your jvagent training signup. "
            "Please confirm these details are correct before I record your signup."
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


async def signup_complete(ctx: HookExecutionContext) -> str:
    values = ctx.extracted_values or {}
    user_name = (values.get("user_name") or "").strip()
    user_email = (values.get("user_email") or "").strip()
    available_times = (values.get("available_times") or "").strip()

    session = ctx.session
    missing = await compute_missing_required(
        session,
        ctx.spec,
        ctx.interview_action._load_fn(ctx.spec),
        ctx.visitor,
        ctx.interview_action,
    )
    if missing or not user_name or not user_email or not available_times:
        ctx.say(
            "Some required signup fields are missing. "
            "Please go back and collect all required information."
        )
        return ctx.tool_response(ok=False, status="validation_failed")

    matched_times: List[str] = []
    if session is not None and isinstance(getattr(session, "context", None), dict):
        raw = session.context.get("matched_training_times") or []
        if isinstance(raw, list):
            matched_times = [str(x) for x in raw if x]

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
        f"Thank you, {user_name}! Your training signup details are recorded locally. "
        f"No booking or notification has been made.{employer_note} {times_note}"
    )
    return ctx.tool_response(ok=True, status="ok")
