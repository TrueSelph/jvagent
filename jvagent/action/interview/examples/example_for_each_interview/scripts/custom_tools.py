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
    # ctx.field_def.key is available in post_processors — no need to hardcode the
    # field name. ctx.value is None here (value is already stored); read from session.
    field_key = ctx.field_def.key
    raw = ctx.session.get_value(field_key) or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    items = [{"id": p, "label": p} for p in parts]
    return ctx.tool_response(ok=True, **ctx.expand_for_each(items))


def validate_quantity(ctx) -> Dict[str, Any]:
    raw = (ctx.value or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        return ctx.invalid("Enter a whole number of at least 1.")
    return ctx.valid(value=raw)


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
