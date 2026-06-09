"""Structured JSON envelopes for interview tools."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

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
    "present_field",
)


def validation_guidance_directive(error: str, *, question_text: str = "") -> str:
    """Build a single user-facing directive from a validator error message."""
    err = (error or "").strip()
    if err.lower().startswith("tell the user:"):
        err = err.split(":", 1)[1].strip()
    if err.lower().startswith("ask:"):
        err = err.split(":", 1)[1].strip()
    body = err
    if question_text:
        body = f"{err} {question_text}".strip()
    return tell_user_directive(body)


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


def tell_user_with_followup_directive(message: str, follow_up_question: str) -> str:
    """Sidebar note plus the next interview question in one user-facing reply."""
    return (
        f"Tell the user: {message} "
        f"Then ask: {follow_up_question} "
        "You may paraphrase slightly but include both the note and the follow-up question."
    )


def review_confirmation_directive(
    summary: str,
    *,
    preamble: str = "Please review your details before we finalize.",
) -> str:
    """Confirmation-step directive — not completion."""
    summary_block = f"\n\n{summary}" if summary else ""
    return (
        f"Tell the user: {preamble}{summary_block} "
        "Ask whether everything looks correct and they want to confirm. "
        "If they want changes, ask what to update. "
        "This is a confirmation step only — the process is NOT complete yet. "
        "Do NOT say the process is complete or that any account or record has been created. "
        "Do NOT call interview__complete until they explicitly confirm. "
        "Do NOT call interview__review again."
    )


def auto_confirm_directive(
    summary: str,
    *,
    preamble: str = "",
) -> str:
    """Review summary shown; chain interview__complete without user confirmation."""
    summary_block = f"\n\n{summary}" if summary else ""
    intro = (preamble or "Here is a summary of what was collected.").strip()
    return (
        f"Tell the user: {intro}{summary_block} "
        "Do not ask whether everything looks correct. "
        "Call interview__complete now in this same turn. "
        "Do NOT call interview__review again."
    )


def call_tool_directive(next_tool: str) -> str:
    """Single-action directive: model should call one interview tool."""
    return f"Call {next_tool}."


def no_session_directive() -> str:
    """Directive when interview tools run without an active session."""
    return (
        "Activate the matching interview skill with use_skill, then call "
        "interview__next_question. Do not ask interview field questions via "
        "reply until the session is active."
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
    present_field: Optional[str] = None,
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
    post_tools_results: Optional[List[Dict[str, Any]]] = None,
    pre_tools_results: Optional[List[Dict[str, Any]]] = None,
    questions: Optional[List[Dict[str, Any]]] = None,
    validators: Optional[List[Dict[str, Any]]] = None,
    custom_tools: Optional[List[str]] = None,
    available_types: Optional[List[str]] = None,
    started_at: Optional[str] = None,
    terminate: Optional[bool] = None,
    custom_message: Optional[str] = None,
    summary: Optional[str] = None,
    review_ready: Optional[bool] = None,
    completion_result: Optional[Dict[str, Any]] = None,
    stored: Optional[bool] = None,
    already_stored: Optional[bool] = None,
    validator: Optional[str] = None,
    validated_from: Optional[str] = None,
    **extra: Any,
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
        "present_field": present_field,
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
        "post_tools_results": post_tools_results,
        "pre_tools_results": pre_tools_results,
        "questions": questions,
        "validators": validators,
        "custom_tools": custom_tools,
        "available_types": available_types,
        "started_at": started_at,
        "terminate": terminate,
        "custom_message": custom_message,
        "summary": summary,
        "review_ready": review_ready,
        "completion_result": completion_result,
        "stored": stored,
        "already_stored": already_stored,
        "validator": validator,
        "validated_from": validated_from,
    }
    for key, val in optional_fields.items():
        if val is not None:
            payload[key] = val
    for key, val in extra.items():
        if val is not None:
            payload[key] = val
    return json.dumps(payload)


def interview_tool_response_from_payload(payload: Dict[str, Any]) -> str:
    """Serialize a pipeline/handler payload dict through interview_tool_response."""
    data = dict(payload)
    ok = data.pop("ok", None)
    status = data.pop("status")
    return interview_tool_response(ok=ok, status=status, **data)
