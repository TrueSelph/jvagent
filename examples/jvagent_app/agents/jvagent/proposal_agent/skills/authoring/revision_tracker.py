"""Track revision markers and their resolution status across the document lifecycle."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__track_revisions",
        "description": (
            "Record and update the status of revision markers. "
            "Call this to initialize the revision list, mark items as resolved, "
            "or get a summary of pending revisions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "resolve", "add"],
                    "description": "Operation: 'list' pending, 'resolve' a marker, or 'add' new markers",
                },
                "revision_id": {
                    "type": "string",
                    "description": "Revision marker ID (required for 'resolve')",
                },
                "markers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique marker ID"},
                            "location": {"type": "string", "description": "Section or location"},
                            "text": {"type": "string", "description": "The review suggestion"},
                            "severity": {
                                "type": "string",
                                "enum": ["suggestion", "warning", "action_required"],
                            },
                        },
                    },
                    "description": "List of markers to add (required for 'add')",
                },
            },
            "required": ["action"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Track revision markers. This is a stateful tracker that works with
    the skill loop's context to persist revision state."""
    action = arguments.get("action", "list")

    # Access the shared skill state for persistence across loop iterations
    skill_state = getattr(visitor, "_skill_state", None)
    if skill_state is None:
        skill_state = {}
        setattr(visitor, "_skill_state", skill_state)

    if action == "add":
        new_markers = arguments.get("markers", [])
        existing = skill_state.get("revision_markers", [])
        skill_state["revision_markers"] = existing + new_markers
        return {
            "action": "add",
            "added": len(new_markers),
            "total_pending": len(
                [m for m in skill_state["revision_markers"] if not m.get("resolved")]
            ),
            "revision_markers": skill_state["revision_markers"],
        }

    elif action == "resolve":
        revision_id = arguments.get("revision_id", "")
        markers = skill_state.get("revision_markers", [])
        resolved = False
        for marker in markers:
            if marker.get("id") == revision_id:
                marker["resolved"] = True
                resolved = True
                break
        return {
            "action": "resolve",
            "revision_id": revision_id,
            "found": resolved,
            "total_pending": len(
                [m for m in markers if not m.get("resolved")]
            ),
        }

    else:  # list
        markers = skill_state.get("revision_markers", [])
        pending = [m for m in markers if not m.get("resolved")]
        resolved = [m for m in markers if m.get("resolved")]
        return {
            "action": "list",
            "total": len(markers),
            "pending": len(pending),
            "resolved": len(resolved),
            "pending_markers": pending,
            "resolved_markers": resolved,
        }
