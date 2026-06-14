"""Structured JSON envelopes and directive strings for interview tools."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from jvagent.action.reply.reply_action import DIRECTIVE_GUIDANCE_MARKER

# Model-only composition guidance lives after this marker in a directive. The
# compose model reads it (it steers rendering); ReplyAction's literal-relay fast
# path drops it so interview internals never reach the user.
_G = DIRECTIVE_GUIDANCE_MARKER

# Keys forwarded from pre/post processor hook results to the LLM.
HOOK_RESULT_KEYS = (
    "ok",
    "status",
    "value",
    "error",
    "error_code",
    "system_message",
    "response_directive",
    "note",
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


def slim_hook_entry(tool: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Build a slim pre/post hook result entry for LLM consumption."""
    entry: Dict[str, Any] = {"tool": tool, "ok": parsed.get("ok", True)}
    for key in HOOK_RESULT_KEYS:
        if key in parsed:
            entry[key] = parsed[key]
    return entry


def tell_user(question: str, *, note: str = "") -> str:
    """Single-action directive: the model replies with one question.

    ``note`` is MODEL-FACING guidance for handling the next step (e.g. "ask for
    the code only; do not resend"). It is placed AFTER the guidance marker so it
    steers the compose model but is never relayed to the user verbatim — callers
    routinely pass tool-call hints and "do not …" rules through it. Anything the
    user must actually read belongs in ``question``.
    """
    guidance = (
        "You may paraphrase slightly but keep the same intent. "
        "Do not ask for other information in this reply."
    )
    if note:
        guidance = f"{guidance} {note}"
    return f"Tell the user: {question}{_G}{guidance}"


def tell_user_with_followup(message: str, follow_up_question: str) -> str:
    """Sidebar note plus the next interview question in one user-facing reply."""
    return (
        f"Tell the user: {message}\n\n{follow_up_question}"
        f"{_G}The text above is a note followed by the next question — deliver both, "
        "in that order. You may paraphrase but keep both the note and the question."
    )


def tell_user_then_call_tool(message: str, next_tool: str) -> str:
    """Sidebar note when no further questions remain; chain a tool in the same turn."""
    return (
        f"Tell the user: {message}"
        f"{_G}You may paraphrase slightly but keep the same intent. "
        f"Then call {next_tool}."
    )


async def tell_user_then_continue(sidebar_message: str) -> str:
    """Sidebar note plus branch-aware next collectible prompt in one directive.

    Must be called from an interview hook (context set by call_hook).
    """
    from .flow import build_next_field
    from .hooks import get_hook_execution_context

    ctx = get_hook_execution_context()
    if ctx is None or ctx.session is None or ctx.spec is None:
        raise RuntimeError(
            "tell_user_then_continue must be called from an interview hook"
        )
    action = ctx.interview_action
    load_fn = action._load_fn(ctx.spec) if action else lambda _: None
    nxt = await build_next_field(ctx.session, ctx.spec, load_fn, ctx.visitor, action)
    prompt = str((nxt or {}).get("prompt") or "").strip()
    if prompt:
        return tell_user_with_followup(sidebar_message, prompt)
    return tell_user_then_call_tool(sidebar_message, "interview__review")


def validation_guidance_directive(error: str, *, question_text: str = "") -> str:
    """Build a single user-facing directive from a validator error message."""
    raw = (error or "").strip()
    lower = raw.lower()
    self_contained = lower.startswith("tell the user:") or lower.startswith("ask:")
    err = raw.split(":", 1)[1].strip() if self_contained else raw
    body = err
    if question_text and not self_contained:
        body = f"{err} {question_text}".strip()
    return tell_user(body)


def review_confirmation_directive(
    summary: str,
    *,
    preamble: str = "Please review your details before we finalize.",
) -> str:
    """Confirmation-step directive — not completion."""
    summary_block = f"\n\n{summary}" if summary else ""
    return (
        f"Tell the user: {preamble}{summary_block}\n\n"
        "Ask whether everything looks correct and whether they want to confirm, "
        "and if they want changes, ask what to update."
        f"{_G}This is a confirmation step only — the process is NOT complete yet. "
        "Do NOT say the process is complete or that any account or record has been created. "
        "Do NOT call interview__complete until they explicitly confirm. "
        "Do NOT call interview__review again."
    )


def auto_confirm_directive(summary: str, *, preamble: str = "") -> str:
    """Review summary shown; chain interview__complete without user confirmation."""
    summary_block = f"\n\n{summary}" if summary else ""
    intro = (preamble or "Here is a summary of what was collected.").strip()
    return (
        f"Tell the user: {intro}{summary_block}"
        f"{_G}Do not ask whether everything looks correct. "
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


def restart_session_directive(interview_type: str) -> str:
    """Directive after complete/cancel when a new interview is needed."""
    return (
        f"Call use_skill with name '{interview_type}' to start a new interview "
        "session, then call interview__next_field."
    )
