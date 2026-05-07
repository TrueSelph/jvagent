"""Shared helpers for skill tool scripts that delegate to actions.

Most skill scripts in ``jvagent/skills/`` are thin wrappers that resolve an
action class via ``visitor.action_resolver`` and forward arguments. The
boilerplate (missing-resolver / missing-action handling) was duplicated
across ~50 files. Use :func:`resolve_action` so the error contract is
consistent and the script body shrinks to a single line of business logic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


async def resolve_action(visitor: Any, class_name: str) -> Tuple[Optional[Any], Optional[Dict[str, str]]]:
    """Resolve *class_name* via the visitor's action resolver.

    Returns ``(action, None)`` on success or ``(None, error_dict)`` so the
    caller can ``return error`` directly without an extra branch.

    The error dict uses a stable shape — ``{"error": "<message>"}`` — that
    matches what existing scripts produce, so adopting this helper does not
    change the LLM-facing tool result format.
    """
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return None, {"error": "ActionResolver not available"}

    action = await resolver.resolve(class_name)
    if action is None:
        return None, {"error": f"{class_name} not found on this agent"}

    return action, None
