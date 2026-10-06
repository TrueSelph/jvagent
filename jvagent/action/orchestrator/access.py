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
    # A missing policy is an intentional open deployment. A configured policy
    # that cannot be loaded is an operational/security failure and must not be
    # confused with absence.
    ac = await agent.get_access_control_action()
    if ac is None:
        return None
    if not ac.policy_applies():
        return None
    return ac


async def is_tool_allowed(
    agent: Any, *, label: str, user_id: Optional[str], channel: str
) -> bool:
    """True if the labelled tool may be dispatched (fail-open / fail-closed)."""
    try:
        ac = await _resolve_access_control(agent)
        if ac is None:
            return True
        allowed = bool(
            await ac.has_action_access(
                user_id=user_id or "",
                action_label=label,
                channel=channel,
            )
        )
        if not allowed:
            logger.info(
                "orchestrator_access_denied",
                extra={
                    "event": "orchestrator_access_denied",
                    "event_code": "orchestrator_access_denied",
                    "action_label": label,
                    "channel": channel,
                    "actor_present": bool(user_id),
                    "stage": "orchestrator",
                    "reason": "policy_denied",
                },
            )
        return allowed
    except Exception as exc:
        # Keep policy failures actionable and queryable without serializing an
        # exception string that may contain database or configuration secrets.
        logger.error(
            "orchestrator_access_policy_failure",
            extra={
                "event": "orchestrator_access_policy_failure",
                "event_code": "orchestrator_access_policy_failure",
                "action_label": label,
                "channel": channel,
                "actor_present": bool(user_id),
                "stage": "orchestrator",
                "reason": "policy_resolution_or_evaluation_error",
                "exception_type": type(exc).__name__,
            },
        )
        return False


__all__ = [
    "delegate_resource_label",
    "is_tool_allowed",
]
