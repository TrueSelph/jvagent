"""Merge and batch-failure helpers for interview tool response directives.

Internal to the interview foundation — used by ``engine`` when folding hook
outputs into a single ``response_directive`` / ``system_message`` envelope.
Skill hooks use ``ctx.say`` / ``ctx.tool_response`` instead; they do not import
this module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from jvagent.action.reply.reply_action import (
    DIRECTIVE_GUIDANCE_MARKER,
    user_facing_directive,
)

from .hooks import append_hint, user_directive, user_followup_directive


def batch_failure_status(failures: List[Dict[str, Any]], *, stored_any: bool) -> str:
    if stored_any:
        return "partial_success"
    if failures and all(
        failure.get("error_code") == "VALIDATION_FAILED" for failure in failures
    ):
        return "validation_failed"
    return "error"


def _reason_from_failure(failure: Dict[str, Any]) -> str:
    direct = str(failure.get("response_directive") or "").strip()
    if direct and not direct.lower().startswith("call "):
        return _normalize_user_directive_text(user_facing_directive(direct))
    raw = str(failure.get("error") or "").strip()
    return _normalize_user_directive_text(user_facing_directive(raw))


def _extra_hint_from_directive(directive: str) -> str:
    """Author hint after the default paraphrase rules, if the directive has one."""
    raw = str(directive or "")
    if DIRECTIVE_GUIDANCE_MARKER not in raw:
        return ""
    guidance = raw.split(DIRECTIVE_GUIDANCE_MARKER, 1)[1]
    _, _, extra = guidance.partition("\n")
    return extra.strip()


def _join_directive_parts(parts: List[str]) -> str:
    message = ""
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        if not message:
            message = text
            continue
        if message[-1] not in ".!?":
            message += "."
        message += f" {text}"
    return message


def _already_contains(haystack: str, needle: str) -> bool:
    n = (needle or "").strip().rstrip(".!?")
    return bool(n) and n.lower() in (haystack or "").lower()


def _single_field_question(prompt: str, reason: str, label: str) -> str:
    """Ask the field, then the reason — no extra 'I still need a valid X' line."""
    if prompt and reason and _already_contains(reason, prompt):
        return reason
    if prompt and reason:
        return _join_directive_parts([prompt, reason])
    if prompt:
        return prompt
    if reason and _already_contains(reason, label):
        return reason
    if reason:
        return _join_directive_parts([f"Please re-enter your {label}.", reason])
    return f"Please re-enter your {label}."


def batch_failure_directive(
    failures: List[Dict[str, Any]], *, stored_any: bool = False
) -> str:
    if not failures:
        return user_directive("Please share the missing information for this process.")
    names = [
        str(f.get("field") or "").strip().replace("_", " ")
        for f in failures
        if f.get("error_code") == "VALIDATION_FAILED"
        and str(f.get("field") or "").strip()
    ]
    first = next(
        (f for f in failures if f.get("error_code") == "VALIDATION_FAILED"),
        failures[0],
    )
    original = str(first.get("response_directive") or "").strip()
    if original.lower().startswith("call "):
        return original
    reason = _reason_from_failure(first)
    prompt = str(first.get("prompt") or "").strip()
    hint = _extra_hint_from_directive(original) or str(first.get("hint") or "").strip()

    if len(names) == 1:
        question = _single_field_question(prompt, reason, names[0])
    elif names:
        question = _join_directive_parts(
            [f"I still need valid values for: {', '.join(names)}.", reason]
        )
    else:
        question = reason or "I still need a bit more information to continue."

    if stored_any:
        directive = user_followup_directive("I've saved the other details.", question)
        return append_hint(directive, hint) if hint else directive
    return user_directive(question, hint=hint)


def append_directive_event(
    queue: List[Dict[str, Any]],
    *,
    field: Optional[str],
    stage: str,
    source: str,
    directive: Optional[str],
) -> None:
    text = str(directive or "").strip()
    if not text:
        return
    queue.append(
        {
            "field": field,
            "stage": stage,
            "source": source,
            "directive": text,
        }
    )


def append_system_event(
    queue: List[Dict[str, Any]],
    *,
    field: Optional[str],
    stage: str,
    source: str,
    system_message: Optional[str],
) -> None:
    text = str(system_message or "").strip()
    if not text:
        return
    queue.append(
        {
            "field": field,
            "stage": stage,
            "source": source,
            "system_message": text,
        }
    )


def _normalize_user_directive_text(directive: str) -> str:
    text = str(directive or "").strip()
    lowered = text.lower()
    if lowered.startswith("tell the user or ask the user:"):
        return text[len("Tell the user or ask the user:") :].strip()
    if lowered.startswith("tell the user:"):
        return text[len("Tell the user:") :].strip()
    if lowered.startswith("ask:"):
        return text[len("Ask:") :].strip()
    return text


def compose_directives(
    queue: List[Dict[str, Any]],
    *,
    fallback: str,
) -> str:
    if not queue:
        return fallback

    user_parts: List[str] = []
    call_parts: List[str] = []
    for item in queue:
        directive = str(item.get("directive") or "").strip()
        if not directive:
            continue
        lowered = directive.lower()
        if lowered.startswith("call "):
            call_parts.append(directive)
            continue
        user_parts.append(_normalize_user_directive_text(directive))

    merged_user: List[str] = []
    for part in user_parts:
        text = part.strip()
        if not text or text in merged_user:
            continue
        merged_user.append(text)

    merged_calls: List[str] = []
    for call in call_parts:
        text = call.strip()
        if not text or text in merged_calls:
            continue
        merged_calls.append(text)

    if not merged_user and not merged_calls:
        return fallback

    if merged_user:
        base = f"Tell the user or ask the user: {' '.join(merged_user)}"
    else:
        base = merged_calls.pop(0)

    for call in merged_calls:
        if call.lower().startswith("call "):
            base = f"{base} Then {call[0].lower() + call[1:]}"
        else:
            base = f"{base} Then {call}"
    return base


def compose_system_message(
    queue: List[Dict[str, Any]],
    *,
    fallback: str = "",
) -> Optional[str]:
    parts: List[str] = []
    for item in queue:
        text = str(item.get("system_message") or "").strip()
        if not text or text in parts:
            continue
        parts.append(text)
    if not parts:
        return fallback or None
    return " ".join(parts)
