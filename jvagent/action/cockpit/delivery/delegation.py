"""Cockpit delegation: resolve routed interact_actions and curate walk path.

When the router classifies one or more ``interact_actions`` (or both
``interact_actions`` + ``skills``), cockpit must hand control over to those
actions via the walker. This module provides the helpers:

- ``resolve_routed_interact_actions`` — turn class-name strings into
  enabled ``InteractAction`` instances on the agent (sorted by weight).
- ``curate_walk_path_for_cockpit`` — restrict the walker's queue to
  ``{cockpit} ∪ {routed IAs} ∪ {always_execute IAs}`` so unrelated
  InteractActions do not run as a side effect of cockpit-as-default.
- ``prepend_routed_interact_actions`` — convenience helper to put the
  routed IAs at the front of the walker queue (used when cockpit yields
  to them after engine completes, or when skipping the engine entirely).

All helpers are defensive: an empty/missing routing result, a missing
actions manager, or a class-name that doesn't resolve to an enabled
InteractAction is logged and skipped — never raised.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional, Set

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing.types import RoutingResult
    from jvagent.action.interact.base import InteractAction
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


async def resolve_routed_interact_actions(
    agent: Any,
    routing: "RoutingResult",
) -> List["InteractAction"]:
    """Resolve routing.interact_actions class names to InteractAction instances.

    Returns instances sorted by ``weight`` ascending so walker visit order
    matches the project-wide convention. Cockpit itself is filtered out
    even if mistakenly classified by the router.
    """
    names = list(routing.interact_actions or [])
    if not agent or not names:
        return []

    try:
        from jvagent.action.interact.base import InteractAction

        actions_mgr = await agent.get_actions_manager()
        if actions_mgr is None:
            return []
        all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
    except Exception as exc:
        logger.warning(
            "cockpit.delegation: failed to enumerate enabled actions: %s", exc
        )
        return []

    wanted: Set[str] = set(names)
    cockpit_class = "CockpitInteractAction"
    matched: List["InteractAction"] = []
    for action in all_enabled:
        if not isinstance(action, InteractAction):
            continue
        cls_name = action.__class__.__name__
        if cls_name == cockpit_class:
            continue
        if cls_name in wanted:
            matched.append(action)

    matched.sort(key=lambda a: int(getattr(a, "weight", 0)))

    missing = wanted - {a.__class__.__name__ for a in matched}
    if missing:
        logger.warning(
            "cockpit.delegation: routed interact_actions not found or disabled: %s",
            sorted(missing),
        )

    return matched


async def collect_always_execute_interact_actions(
    agent: Any,
    *,
    exclude_class_names: Optional[Set[str]] = None,
) -> List["InteractAction"]:
    """Return all enabled InteractActions with ``always_execute=True``.

    Excludes any class names provided in ``exclude_class_names`` (typically the
    cockpit class — cockpit handles its own scheduling).
    """
    if not agent:
        return []
    try:
        from jvagent.action.interact.base import InteractAction

        actions_mgr = await agent.get_actions_manager()
        if actions_mgr is None:
            return []
        all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
    except Exception as exc:
        logger.warning(
            "cockpit.delegation: failed to enumerate always_execute actions: %s",
            exc,
        )
        return []

    excluded = exclude_class_names or set()
    matched: List["InteractAction"] = []
    for action in all_enabled:
        if not isinstance(action, InteractAction):
            continue
        if action.__class__.__name__ in excluded:
            continue
        if bool(getattr(action, "always_execute", False)):
            matched.append(action)
    matched.sort(key=lambda a: int(getattr(a, "weight", 0)))
    return matched


async def curate_walk_path_for_cockpit(
    visitor: "InteractWalker",
    cockpit_action: "InteractAction",
    routed: List["InteractAction"],
    *,
    always_execute: List["InteractAction"],
) -> List["InteractAction"]:
    """Restrict walker queue to ``{cockpit} ∪ routed ∪ always_execute`` (by weight).

    Other enabled InteractActions are removed from the queue. The cockpit
    action itself is preserved at its current position in the curated set so
    revisits (walker-revisit pattern) keep functioning. Returns the curated
    list in walker-visit order.
    """
    seen: Set[str] = set()
    combined: List["InteractAction"] = []

    def _add(action: "InteractAction") -> None:
        ident = getattr(action, "id", None) or action.__class__.__name__
        if ident in seen:
            return
        seen.add(ident)
        combined.append(action)

    _add(cockpit_action)
    for a in routed:
        _add(a)
    for a in always_execute:
        _add(a)

    combined.sort(key=lambda a: int(getattr(a, "weight", 0)))

    try:
        await visitor.curate_walk_path(combined)
    except Exception as exc:
        logger.warning("cockpit.delegation: curate_walk_path failed: %s", exc)
    return combined


async def prepend_routed_interact_actions(
    visitor: "InteractWalker",
    routed: List["InteractAction"],
) -> None:
    """Prepend routed IAs to the walker queue (front of line).

    Use when cockpit is handing off after the engine reaches a terminal step
    (the "both" dispatch mode) or when cockpit is skipping the engine entirely
    in favour of the routed IAs (the "interact_actions only" mode).
    """
    if not routed:
        return
    try:
        # Sort by weight so visit order is deterministic.
        ordered = sorted(routed, key=lambda a: int(getattr(a, "weight", 0)))
        await visitor.prepend(ordered)
    except Exception as exc:
        logger.warning("cockpit.delegation: prepend failed: %s", exc)
