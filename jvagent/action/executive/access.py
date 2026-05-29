"""AccessControl integration for the Executive pattern (ADR-0010 §3 inv. 6).

Two resource labels layer on the existing AC taxonomy
(``skill:{name}`` / ``tool:{name}``):

- ``tool:center:{name}`` — gating a center as an ACTIVATE target.
- ``tool:delegate:{name}`` — gating a rails ``InteractAction`` run inside the
  IA center (uses a stable label so AC policies stay portable across patterns).

Fail-open when no enforcing ``AccessControlAction`` is attached; fail-closed
when AC raises. This module deliberately does **not** import from any other
pattern — the convention is re-implemented here (ADR-0010 §6 isolation
discipline).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def center_resource_label(center_name: str) -> str:
    return f"tool:center:{center_name}"


def delegate_resource_label(action_name: str) -> str:
    return f"tool:delegate:{action_name}"


class ExecutiveAccessDenied(Exception):
    """Raised when AC denies a center activation or rails IA run."""

    def __init__(self, resource: str, *, user_id: Optional[str], channel: str):
        super().__init__(
            f"access denied: {resource} (user_id={user_id}, channel={channel})"
        )
        self.resource = resource
        self.user_id = user_id
        self.channel = channel


async def _resolve_access_control(agent: Any) -> Optional[Any]:
    if agent is None:
        return None
    try:
        ac = await agent.get_access_control_action()
    except Exception as exc:
        logger.debug("executive.access: failed to fetch AccessControlAction: %s", exc)
        return None
    if ac is None:
        return None
    try:
        if not ac.policy_applies():
            return None
    except Exception:
        return None
    return ac


async def _is_allowed(
    ac: Any, *, user_id: Optional[str], channel: str, label: str
) -> bool:
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
            "executive.access: has_action_access raised for label=%s — "
            "failing closed: %s",
            label,
            exc,
        )
        return False


async def check_center_access(
    agent: Any,
    *,
    center_name: str,
    user_id: Optional[str],
    channel: str,
) -> None:
    """Raise :class:`ExecutiveAccessDenied` if the user may not activate the center."""
    ac = await _resolve_access_control(agent)
    if ac is None:
        return
    label = center_resource_label(center_name)
    if not await _is_allowed(ac, user_id=user_id, channel=channel, label=label):
        raise ExecutiveAccessDenied(label, user_id=user_id, channel=channel)


async def check_delegate_access(
    agent: Any,
    *,
    action_name: str,
    user_id: Optional[str],
    channel: str,
) -> None:
    """Raise :class:`ExecutiveAccessDenied` if the user may not run the rails IA."""
    ac = await _resolve_access_control(agent)
    if ac is None:
        return
    label = delegate_resource_label(action_name)
    if not await _is_allowed(ac, user_id=user_id, channel=channel, label=label):
        raise ExecutiveAccessDenied(label, user_id=user_id, channel=channel)


__all__ = [
    "center_resource_label",
    "delegate_resource_label",
    "ExecutiveAccessDenied",
    "check_center_access",
    "check_delegate_access",
]
