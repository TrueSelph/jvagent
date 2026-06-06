"""Structured JSON envelopes for interview tools and session intent classification."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

SessionIntent = Literal["continue", "fresh", "unclear"]

_CONTINUE_PATTERNS = (
    r"\bcontinue\b",
    r"\blet'?s continue\b",
    r"\bgo on\b",
    r"\bresume\b",
    r"\bpick up where\b",
    r"\bsame interview\b",
    r"\byes,? continue\b",
    r"\bcarry on\b",
)

_FRESH_PATTERNS = (
    r"\bnew\b",
    r"\banother\b",
    r"\bdifferent\b",
    r"\bstart over\b",
    r"\bfrom scratch\b",
    r"\bcreate a\b",
    r"\bstart a new\b",
    r"\bnew pre-?alert\b",
    r"\bnew package\b",
    r"\bdifferent tracking\b",
)

# Keys allowed in post_tools_results entries exposed to the LLM.
POST_TOOL_RESULT_KEYS = (
    "ok",
    "status",
    "system_message",
    "exists",
    "skip_to_review",
    "interview_complete",
    "otp_pending",
    "error",
    "error_code",
    "response_directive",
    "next_tool",
)


def tell_user_directive(question: str, *, note: str = "") -> str:
    """Single-action directive: model should reply with one question."""
    text = (
        f"Tell the user: {question} "
        "You may paraphrase slightly but keep the same intent. "
        "Do not ask for other information in this reply."
    )
    if note:
        text += f" {note}"
    return text


def call_tool_directive(next_tool: str) -> str:
    """Single-action directive: model should call one interview tool."""
    return f"Call {next_tool}."


def no_session_directive() -> str:
    """Directive when interview tools run without an active session."""
    return (
        "Activate the interview skill with use_skill, then call "
        "interview__next_question."
    )


def restart_session_directive(interview_type: str) -> str:
    """Directive after complete/cancel when a new interview is needed."""
    return (
        f"Call use_skill with name '{interview_type}' to start a new interview "
        "session, then call interview__next_question."
    )


def tool_observation_failed(obs: str, *, error_code: Optional[str] = None) -> bool:
    """True when a tool observation string indicates failure."""
    if not obs:
        return True
    if error_code and error_code in obs:
        return True
    try:
        parsed = json.loads(obs)
        if isinstance(parsed, dict):
            if parsed.get("ok") is False:
                return True
            if error_code and parsed.get("error_code") == error_code:
                return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def directive_for_missing_fields(
    next_questions: Optional[List[Dict[str, Any]]],
    missing_required: List[str],
) -> tuple[str, Optional[str]]:
    """Pick one directive after a field is stored or skipped."""
    if next_questions:
        question = next_questions[0].get("question", "")
        if question:
            return tell_user_directive(question), None
    if not missing_required:
        return call_tool_directive("interview__review"), "interview__review"
    return call_tool_directive("interview__review"), "interview__review"


def classify_user_session_intent(user_message: str) -> SessionIntent:
    """Classify latest user message as continue, fresh, or unclear."""
    text = (user_message or "").strip().lower()
    if not text:
        return "unclear"

    continue_hit = any(re.search(p, text) for p in _CONTINUE_PATTERNS)
    fresh_hit = any(re.search(p, text) for p in _FRESH_PATTERNS)

    if continue_hit and not fresh_hit:
        return "continue"
    if fresh_hit and not continue_hit:
        return "fresh"
    if continue_hit and fresh_hit:
        return "unclear"
    return "unclear"


def slim_post_tool_entry(tool: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Build a slim post_tools_results entry for LLM consumption."""
    entry: Dict[str, Any] = {"tool": tool, "ok": parsed.get("ok", True)}
    for key in POST_TOOL_RESULT_KEYS:
        if key in parsed:
            entry[key] = parsed[key]
    return entry


def interview_tool_response(
    *,
    ok: Optional[bool] = None,
    status: str,
    system_message: Optional[str] = None,
    response_directive: Optional[str] = None,
    next_tool: Optional[str] = None,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    exists: Optional[bool] = None,
    skip_to_review: Optional[bool] = None,
    interview_complete: Optional[bool] = None,
    otp_pending: Optional[bool] = None,
    interview_type: Optional[str] = None,
    fields: Optional[Dict[str, Any]] = None,
    missing_required: Optional[List[str]] = None,
    skipped_fields: Optional[List[str]] = None,
    next_questions: Optional[List[Dict[str, Any]]] = None,
    field: Optional[str] = None,
    value: Optional[Any] = None,
    valid: Optional[bool] = None,
    fresh_session: Optional[bool] = None,
    seeded_fields: Optional[List[str]] = None,
    post_tools_results: Optional[List[Dict[str, Any]]] = None,
    pre_tools_results: Optional[List[Dict[str, Any]]] = None,
    questions: Optional[List[Dict[str, Any]]] = None,
    validators: Optional[List[Dict[str, Any]]] = None,
    custom_tools: Optional[List[str]] = None,
    available_types: Optional[List[str]] = None,
    terminate: Optional[bool] = None,
    custom_message: Optional[str] = None,
) -> str:
    """Build a consistent JSON tool response string.

    system_message: context for the model about what happened (not a user reply).
    response_directive: what the model should do next.
    """
    if ok is None:
        ok = status not in ("error", "validation_failed")
    payload: Dict[str, Any] = {"ok": ok, "status": status}
    optional_fields: Dict[str, Any] = {
        "system_message": system_message,
        "response_directive": response_directive,
        "next_tool": next_tool,
        "error": error,
        "error_code": error_code,
        "exists": exists,
        "skip_to_review": skip_to_review,
        "interview_complete": interview_complete,
        "otp_pending": otp_pending,
        "interview_type": interview_type,
        "fields": fields,
        "missing_required": missing_required,
        "skipped_fields": skipped_fields,
        "next_questions": next_questions,
        "field": field,
        "value": value,
        "valid": valid,
        "fresh_session": fresh_session,
        "seeded_fields": seeded_fields,
        "post_tools_results": post_tools_results,
        "pre_tools_results": pre_tools_results,
        "questions": questions,
        "validators": validators,
        "custom_tools": custom_tools,
        "available_types": available_types,
        "terminate": terminate,
        "custom_message": custom_message,
    }
    for key, val in optional_fields.items():
        if val is not None:
            payload[key] = val
    return json.dumps(payload)


def interview_step_response(*, ok: bool, status: str, **fields: Any) -> str:
    """Build a step response; delegates to interview_tool_response."""
    return interview_tool_response(ok=ok, status=status, **fields)


def resolve_interview_session_intent(
    user_message: str,
    session: Any,
    interview_type: str,
) -> Dict[str, Any]:
    """Map session state + user message to status, next_tool, and directives."""
    from .session import InterviewStatus

    intent = classify_user_session_intent(user_message)

    if session and getattr(session, "is_active", None) and session.is_active():
        return {
            "status": "in_progress",
            "intent": intent,
            "interview_type": getattr(session, "interview_type", interview_type),
            "next_tool": None,
            "force_fresh": False,
            "response_directive": call_tool_directive("interview__get_status"),
        }

    status_val = None
    if session and hasattr(session, "status"):
        status_val = (
            session.status.value
            if hasattr(session.status, "value")
            else str(session.status)
        )

    if status_val in (
        InterviewStatus.CANCELLED.value,
        InterviewStatus.COMPLETED.value,
    ):
        return {
            "status": "start_new",
            "intent": "fresh",
            "interview_type": interview_type,
            "next_tool": None,
            "force_fresh": False,
            "response_directive": restart_session_directive(interview_type),
        }

    return {
        "status": "start_new",
        "intent": intent,
        "interview_type": interview_type,
        "next_tool": None,
        "force_fresh": False,
        "response_directive": restart_session_directive(interview_type),
    }
