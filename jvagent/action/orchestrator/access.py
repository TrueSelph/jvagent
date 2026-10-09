"""AccessControl for the Orchestrator (ADR-0012 inv. 6).

Tool dispatch is gated on the existing AC taxonomy; IA-as-tools use the stable
``tool:delegate:{action_name}`` label. Fail-open when no enforcing
``AccessControlAction`` is attached; fail-closed when AC raises.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def delegate_resource_label(action_name: str) -> str:
    return f"tool:delegate:{action_name}"


async def _resolve_access_control(agent: Any) -> Optional[Any]:
    if agent is None:
        return None
    try:
        ac = await agent.get_access_control_action()
    except Exception as exc:
        logger.debug("orchestrator.access: failed to fetch AC: %s", exc)
        return None
    if ac is None:
        return None
    try:
        if not ac.policy_applies():
            return None
    except Exception:
        return None
    return ac


async def drop_unpermitted_tools(
    ac: Any,
    *,
    user_id: Optional[str],
    channel: str,
    tools: Dict[str, Any],
    visible: set,
    longtail: Optional[set] = None,
) -> None:
    """Remove tools listed in ``permissions[channel].tools`` the sender cannot call.

    A pin cannot keep them. They leave the callable map, so discovery cannot
    load them either. Names that are not in that map stay.
    """
    names = ac.tool_permission_names(channel)
    for name in names:
        if name not in tools:
            continue
        try:
            allowed = bool(await ac.has_tool_access(user_id or "", name, channel))
        except Exception as exc:
            logger.warning(
                "orchestrator.access: has_tool_access raised for %s — hiding: %s",
                name,
                exc,
            )
            allowed = False
        if allowed:
            continue
        tools.pop(name, None)
        visible.discard(name)
        if longtail is not None:
            longtail.discard(name)


async def is_named_tool_allowed(
    agent: Any, *, tool_name: str, user_id: Optional[str], channel: str
) -> bool:
    """True if ``permissions[channel].tools[tool_name]`` allows this sender.

    Opted-in tools fail closed: no enforcing AccessControl, a missing ``tools``
    entry, or no matching allow rule denies the call. This does not use the
    action-label gate.
    """
    ac = await _resolve_access_control(agent)
    if ac is None:
        return False
    check = getattr(ac, "has_tool_access", None)
    if not callable(check):
        return False
    try:
        return bool(await check(user_id or "", tool_name, channel))
    except Exception as exc:
        logger.warning(
            "orchestrator.access: has_tool_access raised for %s — "
            "failing closed: %s",
            tool_name,
            exc,
        )
        return False


async def is_tool_allowed(
    agent: Any, *, label: str, user_id: Optional[str], channel: str
) -> bool:
    """True if the labelled tool may be dispatched (fail-open / fail-closed)."""
    ac = await _resolve_access_control(agent)
    if ac is None:
        return True
    try:
        return bool(
            await ac.has_action_access(
                user_id=user_id or "",
                action_label=label,
                channel=channel,
            )
        )
    except Exception as exc:
        logger.warning(
            "orchestrator.access: has_action_access raised for %s — "
            "failing closed: %s",
            label,
            exc,
        )
        return False


__all__ = [
    "delegate_resource_label",
    "drop_unpermitted_tools",
    "is_named_tool_allowed",
    "is_tool_allowed",
]
