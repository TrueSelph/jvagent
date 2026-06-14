"""AccessControl for the Orchestrator (ADR-0012 inv. 6).

Tool dispatch is gated on the existing AC taxonomy; IA-as-tools use the stable
``tool:delegate:{action_name}`` label. Fail-open when no enforcing
``AccessControlAction`` is attached; fail-closed when AC raises.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

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
    "is_tool_allowed",
]
