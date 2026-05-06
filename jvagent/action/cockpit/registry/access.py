"""Cockpit access control integration.

Per-user access checks for skills, interact_actions, and individual tools
inside the cockpit. Delegates to the agent's ``AccessControlAction.has_action_access``
so cockpit reuses the standard ``(user_id, channel, resource)`` policy and the
existing channel-level rules continue to work unchanged.

Resource taxonomy (the ``action_label`` passed to ``has_action_access``):

- **Interact actions**: the class name (e.g. ``HandoffInteractAction``). This
  matches the legacy convention so existing rules continue to work.
- **Skills**: ``skill:{name}`` (e.g. ``skill:web_search``).
- **Tools**: ``tool:{tool_name}`` (e.g. ``tool:web_search__search``,
  ``tool:memory_set``). Both action-tool and harness-tool names are filtered
  the same way.

If the agent has no ``AccessControlAction`` attached, or it isn't enforcing,
all checks pass (parity with existing harness behaviour).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing.types import RoutingResult
    from jvagent.action.interact.base import InteractAction
    from jvagent.tooling.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resource label helpers — single source of truth for the taxonomy.
# ---------------------------------------------------------------------------


def skill_resource_label(skill_name: str) -> str:
    return f"skill:{skill_name}"


def tool_resource_label(tool_name: str) -> str:
    return f"tool:{tool_name}"


def interact_action_resource_label(class_name: str) -> str:
    # Matches the existing convention used by jvagent/access_control rules.
    return class_name


# ---------------------------------------------------------------------------
# Access control resolution
# ---------------------------------------------------------------------------


async def _resolve_access_control(agent: Any) -> Optional[Any]:
    """Return the agent's AccessControlAction if it exists and is enforcing."""
    if agent is None:
        return None
    try:
        ac = await agent.get_access_control_action()
    except Exception as exc:
        logger.debug("cockpit.access: failed to fetch AccessControlAction: %s", exc)
        return None
    if ac is None:
        return None
    if not getattr(ac, "policy_applies", lambda: True)():
        return None
    return ac


async def _is_allowed(
    ac: Any,
    *,
    user_id: Optional[str],
    channel: str,
    label: str,
) -> bool:
    """Single-shot access check; falls back to allow when ac is None or check raises."""
    if ac is None:
        return True
    try:
        return await ac.has_action_access(
            user_id=user_id or "",
            action_label=label,
            channel=channel or "default",
        )
    except Exception as exc:
        logger.warning(
            "cockpit.access: has_action_access raised for label=%s user=%s: %s",
            label,
            user_id,
            exc,
        )
        # Fail closed if AC is enforcing but the call errored — denying a
        # resource is safer than accidentally admitting a denied user.
        return False


# ---------------------------------------------------------------------------
# Filters used by the cockpit dispatch + tool assembly paths
# ---------------------------------------------------------------------------


async def filter_routed_skills_by_access(
    agent: Any,
    routing: "RoutingResult",
    *,
    user_id: Optional[str],
    channel: str,
) -> List[str]:
    """Strip skill names the user can't access. Returns the surviving list."""
    skills = list(routing.actions or [])
    if not skills:
        return skills

    ac = await _resolve_access_control(agent)
    if ac is None:
        return skills

    allowed: List[str] = []
    for skill in skills:
        if await _is_allowed(
            ac,
            user_id=user_id,
            channel=channel,
            label=skill_resource_label(skill),
        ):
            allowed.append(skill)
        else:
            logger.info(
                "cockpit.access: denying skill=%s for user=%s channel=%s",
                skill,
                user_id,
                channel,
            )
    return allowed


async def filter_routed_interact_actions_by_access(
    agent: Any,
    actions: List["InteractAction"],
    *,
    user_id: Optional[str],
    channel: str,
) -> List["InteractAction"]:
    """Strip routed InteractAction instances the user can't access."""
    if not actions:
        return actions

    ac = await _resolve_access_control(agent)
    if ac is None:
        return actions

    allowed: List["InteractAction"] = []
    for action in actions:
        cls_name = action.__class__.__name__
        if await _is_allowed(
            ac,
            user_id=user_id,
            channel=channel,
            label=interact_action_resource_label(cls_name),
        ):
            allowed.append(action)
        else:
            logger.info(
                "cockpit.access: denying interact_action=%s for user=%s channel=%s",
                cls_name,
                user_id,
                channel,
            )
    return allowed


async def filter_tool_registry_by_access(
    registry: "ToolRegistry",
    agent: Any,
    *,
    user_id: Optional[str],
    channel: str,
) -> int:
    """Remove tools the user can't access from ``registry``. Returns count removed."""
    ac = await _resolve_access_control(agent)
    if ac is None:
        return 0

    removed = 0
    for name in list(registry.names()):
        if not await _is_allowed(
            ac,
            user_id=user_id,
            channel=channel,
            label=tool_resource_label(name),
        ):
            registry.remove(name)
            removed += 1
            logger.info(
                "cockpit.access: removing tool=%s for user=%s channel=%s",
                name,
                user_id,
                channel,
            )
    return removed
