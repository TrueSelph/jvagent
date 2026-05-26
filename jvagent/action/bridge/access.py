"""AccessControl integration for Bridge.

Bridge layers two new resource labels on top of the existing AC taxonomy
(see ``jvagent.action.cockpit.registry.access`` for parallel cockpit
conventions ``skill:{name}`` / ``tool:{name}``):

- ``tool:helm:{helm_name}`` — gating a helm as a ``SHIFT`` target.
- ``tool:delegate:{action_name}`` — gating a rails ``InteractAction`` as a
  ``DELEGATE`` target.

If the agent has no ``AccessControlAction`` attached (or it is not
enforcing), both checks pass — matching cockpit's existing fail-open
default. When AC raises an exception the check fails closed (denied), again
matching cockpit's posture for security-relevant resources.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def helm_resource_label(helm_name: str) -> str:
    """Resource string used in AC rules for shift gating."""
    return f"tool:helm:{helm_name}"


def delegate_resource_label(action_name: str) -> str:
    """Resource string used in AC rules for DELEGATE gating."""
    return f"tool:delegate:{action_name}"


class BridgeAccessDenied(Exception):
    """Raised by ``check_helm_access`` / ``check_delegate_access`` when AC denies.

    Carries the resource label so Bridge can build a structured fallback
    response. Bridge SHOULD NOT propagate this exception to the walker — it
    catches and routes to the configured safe-fallback path instead.
    """

    def __init__(self, resource: str, *, user_id: Optional[str], channel: str):
        super().__init__(
            f"access denied: {resource} (user_id={user_id}, channel={channel})"
        )
        self.resource = resource
        self.user_id = user_id
        self.channel = channel


async def _resolve_access_control(agent: Any) -> Optional[Any]:
    """Return the agent's AccessControlAction iff present and enforcing."""
    if agent is None:
        return None
    try:
        ac = await agent.get_access_control_action()
    except Exception as exc:
        logger.debug("bridge.access: failed to fetch AccessControlAction: %s", exc)
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
    ac: Any,
    *,
    user_id: Optional[str],
    channel: str,
    label: str,
) -> bool:
    """Single-shot access check. Fails closed on AC exception."""
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
            "bridge.access: AccessControlAction.has_action_access raised for "
            "label=%s — failing closed: %s",
            label,
            exc,
        )
        return False


async def check_helm_access(
    agent: Any,
    *,
    helm_name: str,
    user_id: Optional[str],
    channel: str,
) -> None:
    """Raise ``BridgeAccessDenied`` if user is denied the target helm.

    Returns None on allow (or when no AccessControlAction is enforcing).
    """
    ac = await _resolve_access_control(agent)
    if ac is None:
        return
    label = helm_resource_label(helm_name)
    allowed = await _is_allowed(ac, user_id=user_id, channel=channel, label=label)
    if not allowed:
        raise BridgeAccessDenied(label, user_id=user_id, channel=channel)


async def check_delegate_access(
    agent: Any,
    *,
    action_name: str,
    user_id: Optional[str],
    channel: str,
) -> None:
    """Raise ``BridgeAccessDenied`` if user is denied the DELEGATE target.

    Returns None on allow (or when no AccessControlAction is enforcing).
    """
    ac = await _resolve_access_control(agent)
    if ac is None:
        return
    label = delegate_resource_label(action_name)
    allowed = await _is_allowed(ac, user_id=user_id, channel=channel, label=label)
    if not allowed:
        raise BridgeAccessDenied(label, user_id=user_id, channel=channel)
