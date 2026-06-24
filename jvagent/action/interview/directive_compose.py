"""Merge and batch-failure helpers for interview tool response directives.

Internal to the interview foundation — used by ``engine`` when folding hook
outputs into a single ``response_directive`` / ``system_message`` envelope.
Skill hooks use ``ctx.say`` / ``ctx.tool_response`` instead; they do not import
this module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .hooks import user_directive


def batch_failure_status(failures: List[Dict[str, Any]], *, stored_any: bool) -> str:
    if stored_any:
        return "partial_success"
    if failures and all(
        failure.get("error_code") == "VALIDATION_FAILED" for failure in failures
    ):
        return "validation_failed"
    return "error"


def batch_failure_directive(failures: List[Dict[str, Any]]) -> str:
    if not failures:
        return user_directive("Please share the missing information for this process.")
    if len(failures) == 1:
        direct = str(failures[0].get("response_directive") or "").strip()
        if direct:
            return direct
    names = [
        str(f.get("field") or "").strip().replace("_", " ")
        for f in failures
        if f.get("error_code") == "VALIDATION_FAILED"
        and str(f.get("field") or "").strip()
    ]
    fields_text = ", ".join(name for name in names if name)
    user_error = next(
        (
            str(f.get("error") or "").strip()
            for f in failures
            if f.get("error_code") == "VALIDATION_FAILED"
            and str(f.get("error") or "").strip()
        ),
        "",
    )
    if fields_text:
        message = f"I still need valid values for: {fields_text}."
    else:
        message = "I still need a bit more information to continue."
    if user_error:
        message = f"{message} {user_error}"
    return user_directive(message)


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
        return text[len("Tell the user or ask the user:"):].strip()
    if lowered.startswith("tell the user:"):
        return text[len("Tell the user:"):].strip()
    if lowered.startswith("ask:"):
        return text[len("Ask:"):].strip()
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
