"""Custom tools for example_for_each_interview."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def validate_item_ids(ctx) -> Dict[str, Any]:
    raw = (ctx.value or "").strip()
    if not raw:
        return ctx.invalid("Provide at least one item ID.")
    parts = [p.strip() for p in re.split(r"[,;\n]+", raw) if p.strip()]
    if not parts:
        return ctx.invalid("Provide at least one item ID.")
    deduped: List[str] = []
    seen: set[str] = set()
    for part in parts:
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", part):
            return ctx.invalid(f"Invalid item ID: {part!r}")
        key = part.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(part)
    return ctx.valid(value=", ".join(deduped))


def expand_item_ids(ctx) -> str:
    field_key = ctx.field_def.key
    raw = ctx.session.get_value(field_key) or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    items = [{"id": p, "label": p} for p in parts]
    return ctx.tool_response(ok=True, **ctx.expand_for_each(items))


def item_id_prefix(
    index: int, total: int, label: str, field_key: str, field_value: str
) -> str:
    from jvagent.action.interview.for_each import _ordinal, _singularize_key

    if total == 1:
        return ""
    singular = _singularize_key(field_key)
    return (
        f"For the {_ordinal(index)} {singular} {field_value} "
        "(state this prefix only when clarity needs it; "
        "do not repeat it for every subpart question of the same item):"
    )


def validate_quantity(ctx) -> Dict[str, Any]:
    raw = (ctx.value or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        return ctx.invalid("Enter a whole number of at least 1.")
    return ctx.valid(value=raw)


_NOTES_DECLINED_ALL_KEY = "_notes_declined_all"
_NOTES_DECLINE_PHRASES = ("no notes", "skip notes", "no extra notes", "none for any")


async def skip_notes_if_declined(ctx) -> Dict[str, Any]:
    """Ask-time pre_processor on the optional ``notes`` child field.

    When the user declines notes for the whole batch — in the current message or
    recorded earlier in this interview — skip the field instead of asking. The
    batch-wide flag makes the decline stick across every for_each item, including
    items the interview advances to inside a single set_fields call (which is
    where a purely per-item skip used to re-ask the second item once).
    """
    session = ctx.session
    field_def = ctx.field_def
    if session is None or field_def is None:
        return ctx.tool_response(ok=True, status="ok")

    declined = bool(session.context.get(_NOTES_DECLINED_ALL_KEY))
    if not declined:
        for text in (
            str(getattr(ctx.visitor, "utterance", "") or ""),
            str(ctx.activation_utterance or ""),
        ):
            low = text.lower()
            if low and any(p in low for p in _NOTES_DECLINE_PHRASES):
                declined = True
                session.context[_NOTES_DECLINED_ALL_KEY] = True
                break

    if not declined:
        return ctx.tool_response(ok=True, status="ok")

    session.skip_field(field_def.key)
    return ctx.tool_response(ok=True, status="ok")


def for_each_review(ctx) -> str:
    # Use ctx.get_for_each_records() instead of accessing session.context internals.
    records = ctx.get_for_each_records("item_ids")
    lines = [f"Registered {len(records)} item(s)."]
    return ctx.tool_response(ok=True, custom_message="\n".join(lines))


async def for_each_complete(ctx) -> str:
    records = ctx.get_for_each_records("item_ids")
    ctx.session.context["registered_items"] = records
    return ctx.tool_response(
        ok=True,
        retain_context_keys=["registered_items"],
        response_directive=f"Registered {len(records)} item(s).",
    )
