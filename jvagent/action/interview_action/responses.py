"""Structured JSON envelopes and directive strings for interview tools."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Keys forwarded from pre/post processor hook results to the LLM.
HOOK_RESULT_KEYS = (
    "ok",
    "status",
    "value",
    "error",
    "error_code",
    "system_message",
    "response_directive",
    "next_tool",
    "interview_complete",
)


def interview_tool_response(
    *, ok: Optional[bool] = None, status: str, **data: Any
) -> str:
    """Serialize a tool response envelope; None values are dropped.

    system_message: context for the model about what happened (not a user reply).
    response_directive: what the model should do next.
    """
    if ok is None:
        ok = status not in ("error", "validation_failed")
    payload: Dict[str, Any] = {"ok": ok, "status": status}
    payload.update({k: v for k, v in data.items() if v is not None})
    return json.dumps(payload)


def interview_tool_response_from_payload(payload: Dict[str, Any]) -> str:
    data = dict(payload)
    ok = data.pop("ok", None)
    status = data.pop("status")
    return interview_tool_response(ok=ok, status=status, **data)


def slim_hook_entry(tool: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Build a slim pre/post hook result entry for LLM consumption."""
    entry: Dict[str, Any] = {"tool": tool, "ok": parsed.get("ok", True)}
    for key in HOOK_RESULT_KEYS:
        if key in parsed:
            entry[key] = parsed[key]
    return entry


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


def validation_guidance_directive(error: str, *, question_text: str = "") -> str:
    """Build a single user-facing directive from a validator error message."""
    raw = (error or "").strip()
    lower = raw.lower()
    self_contained = lower.startswith("tell the user:") or lower.startswith("ask:")
    err = raw.split(":", 1)[1].strip() if self_contained else raw
    body = err
    if question_text and not self_contained:
        body = f"{err} {question_text}".strip()
    return tell_user_directive(body)


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


def auto_confirm_directive(summary: str, *, preamble: str = "") -> str:
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
        "interview__next_field. Do not ask interview field questions via "
        "reply until the session is active."
    )


def build_field_awareness_message(awaiting_fields: List[Dict[str, Any]]) -> str:
    """Human-readable line for events, pending_directive, and field_awareness envelopes."""
    if not awaiting_fields:
        return ""
    primary = awaiting_fields[0]
    key = str(primary.get("key", "")).strip()
    if not key:
        return ""
    prompt = str(primary.get("prompt", "")).strip()
    message = f"Awaiting user input for '{key}' field."
    if prompt:
        message += f" Question: {prompt}"
    if len(awaiting_fields) > 1:
        extras = [
            str(entry.get("key", "")).strip()
            for entry in awaiting_fields[1:]
            if entry.get("key")
        ]
        if extras:
            also = ", ".join(f"'{name}'" for name in extras)
            message += f" Also awaiting: {also}."
    return message


def restart_session_directive(interview_type: str) -> str:
    """Directive after complete/cancel when a new interview is needed."""
    return (
        f"Call use_skill with name '{interview_type}' to start a new interview "
        "session, then call interview__next_field."
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
