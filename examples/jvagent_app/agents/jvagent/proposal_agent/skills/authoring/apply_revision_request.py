"""Apply straightforward revision requests to markdown text."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__apply_revision_request",
        "description": (
            "Apply deterministic text replacements for user-requested revisions. "
            "For complex rewrites, return instruction payload for model-driven editing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "revision_request": {"type": "string"},
                "replace_pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["content", "revision_request"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    content = arguments.get("content", "")
    revision_request = arguments.get("revision_request", "")
    replace_pairs = arguments.get("replace_pairs", []) or []
    updated = content
    replacements_applied = 0
    for pair in replace_pairs:
        old = pair.get("old")
        new = pair.get("new", "")
        if old and old in updated:
            updated = updated.replace(old, new)
            replacements_applied += 1

    return {
        "updated_content": updated,
        "replacements_applied": replacements_applied,
        "revision_request": revision_request,
        "needs_model_rewrite": replacements_applied == 0,
        "message": (
            "If needs_model_rewrite is true, rewrite affected sections using revision_request "
            "and keep unchanged sections intact."
        ),
    }
