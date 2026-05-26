"""Turn-lock detection + interrupt gating for Bridge (BRIDGE-ROADMAP §F).

A *turn-locked* action is one whose :class:`Manifest` declares
``turn_lock: true`` — typically a multi-turn flow like an interview or
form-fill. While a turn-locked action is in flight, other helms should
defer to it (DELEGATE) rather than running their own model loop in
parallel and confusing the user.

Two surfaces:

1. :func:`find_turn_lock_owner` — given a visitor, return the
   ``InteractAction`` whose ``manifest.turn_lock`` is True AND which has
   been recorded as executing in the recent interaction history. ``None``
   when no lock is active.

2. :func:`is_interrupt_allowed` — given a helm and a verb, return True
   iff ``SHIFT(interrupt=True)`` should be honoured. Gated by the helm's
   ``can_interrupt`` flag (set on ``BaseHelm`` subclasses + mirrored in
   the helm's manifest).

State across turns (full lock-holder tracking with explicit release on
``EMIT(finalize=True)``) is deferred — F-minimum ships the detection
primitive and the interrupt gate so Bridge can enforce ADR-0007's
``interrupt`` semantics today. Full state machine is a follow-up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

from jvagent.action.manifest import Manifest

if TYPE_CHECKING:
    from jvagent.action.helm.base import BaseHelm
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnLockOwner:
    """Description of an active turn-locked action.

    ``action`` is the resolved ``InteractAction`` instance; ``action_name``
    is the class name (matches what Bridge's DELEGATE verb expects);
    ``manifest`` is the parsed manifest so callers don't need to re-read
    it.
    """

    action_name: str
    action: Any
    manifest: Manifest


async def find_turn_lock_owner(
    visitor: "InteractWalker",
    *,
    lookback_turns: int = 3,
) -> Optional[TurnLockOwner]:
    """Return the active turn-locked action on ``visitor``, or ``None``.

    Algorithm:
    1. Pull the last ``lookback_turns`` interactions from the conversation.
    2. For each interaction, collect the action names it recorded in
       ``interaction.actions``.
    3. For each unique action, resolve the live instance on the agent's
       actions manager and read its manifest.
    4. Return the FIRST action whose ``manifest.turn_lock`` is True.

    The lookback bound is configurable but small — turn-locks are
    expected to span a handful of turns, not a whole conversation.
    Returns ``None`` (no lock) on any error or absence.
    """
    conversation = getattr(visitor, "conversation", None)
    if conversation is None:
        return None

    interaction = getattr(visitor, "interaction", None)
    excluded_id = getattr(interaction, "id", None) if interaction else None

    # ``get_interaction_history`` formats by default and does NOT include
    # ``interaction.actions`` in either the formatted or raw output —
    # turn-lock detection needs that field. Read raw Interaction nodes
    # via ``get_interactions`` so ``.actions`` is reachable directly.
    try:
        interactions = await conversation.get_interactions(
            limit=max(1, int(lookback_turns)),
            reverse=True,
        )
    except Exception as exc:
        logger.debug("turn_lock: history fetch failed: %s", exc)
        return None

    if not interactions:
        return None

    action_names: List[str] = []
    seen: set = set()
    for inter in interactions:
        if excluded_id and getattr(inter, "id", None) == excluded_id:
            continue
        for name in getattr(inter, "actions", None) or []:
            if name in seen:
                continue
            seen.add(name)
            action_names.append(name)

    if not action_names:
        return None

    agent = await _resolve_agent_via_visitor(visitor)
    if agent is None:
        return None

    actions_by_class = await _index_actions_by_class_name(agent)
    if not actions_by_class:
        return None

    for name in action_names:
        action = actions_by_class.get(name)
        if action is None:
            continue
        try:
            manifest = action.get_manifest()
        except Exception:
            continue
        if manifest.turn_lock:
            return TurnLockOwner(
                action_name=name,
                action=action,
                manifest=manifest,
            )
    return None


def is_interrupt_allowed(helm: "BaseHelm") -> bool:
    """Return True iff the helm is permitted to emit ``SHIFT(interrupt=True)``.

    Gated by the helm's ``can_interrupt`` attribute. The helm's manifest
    is intentionally NOT consulted here — operators configure this via
    the helm's BaseHelm field so it's overridable in ``agent.yaml``
    without modifying the manifest block. Manifests describe; attributes
    decide.
    """
    return bool(getattr(helm, "can_interrupt", False))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_agent_via_visitor(visitor: "InteractWalker") -> Any:
    """Best-effort lookup of the agent node the walker is visiting.

    Walkers don't carry a direct ``agent`` reference on every code path;
    we try ``visitor.agent`` first, then a couple of well-known
    attributes. Returns ``None`` when no agent is reachable — caller
    falls back to "no lock detected".
    """
    for attr in ("agent", "_agent", "interact_agent"):
        agent = getattr(visitor, attr, None)
        if agent is not None:
            return agent
    # Walker may carry an ``agent_id`` and the actions manager lookup is
    # cheap enough to attempt directly via Agent.get, but to avoid pulling
    # the Agent model into this module we just return None here. Callers
    # treat None as "no lock detectable" — safe degradation.
    return None


async def _index_actions_by_class_name(agent: Any) -> dict:
    """Build a ``{class_name: action_instance}`` index from the agent.

    Returns an empty dict on any error.
    """
    try:
        actions_mgr = await agent.get_actions_manager()
        if actions_mgr is None:
            return {}
        all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
    except Exception as exc:
        logger.debug("turn_lock: actions enumeration failed: %s", exc)
        return {}

    out: dict = {}
    for action in all_enabled:
        try:
            out[action.__class__.__name__] = action
        except Exception:
            continue
    return out


__all__ = [
    "TurnLockOwner",
    "find_turn_lock_owner",
    "is_interrupt_allowed",
]
