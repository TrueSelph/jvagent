"""Single-container state object for reasoning-helm-owned ``visitor._skill_state``.

Duplicated from ``jvagent/action/cockpit/session.py`` at commit ``4bc6db6``
as part of C-2 (BRIDGE-ROADMAP §C). Zero imports from
``jvagent.action.cockpit`` per the C-strategy hard constraint. ``SESSION_KEY``
remains a constant so the duplicated session object slots into
``visitor._skill_state`` independently of the standalone Cockpit — Bridge installs separately
from the standalone Cockpit (PATTERNS.md forbids coexistence on one agent).

NOTE: The ReasoningHelm reuses ``visitor._skill_state`` for engine state
because (a) the engine code expects it (faithful duplication minimises diff
risk) and (b) Bridge owns ``visitor._bridge_state`` for the helm-level
orchestration concerns. Two distinct attributes; no overlap.

Original docstring follows.



``EngineSession`` consolidates all engine-owned per-run state into one
dataclass on ``visitor._skill_state`` under :data:`SESSION_KEY`. A single
:func:`clear_session` call resets every field at once — no per-key
bookkeeping when stale state needs flushing across walker visits.

Skill-system keys (``discovered_skills``, ``skill_catalog``,
``engine_skill_load_report``, ``interact_walker``, ``action_resolver``,
``action``) are NOT folded in — they're shared with the broader skill /
visitor ecosystem and aren't owned by the engine alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The single canonical key on ``visitor._skill_state``. Imported by the
# helm, engine, and tools — there is no other place to store engine-
# owned per-run state.
SESSION_KEY = (
    "reasoning_helm_session"  # distinct from the standalone Cockpit's "cockpit_session"
)
# so a misconfigured agent with both patterns would not silently share state.
# Bridge + Cockpit coexistence is forbidden by PATTERNS.md at the scaffolder
# level, but defensively isolating the key prevents accidental cross-talk.


@dataclass
class EngineSession:
    """All engine-owned per-run state, in one place.

    Lifecycle:
    - First visit (Phase 1 routing) → :func:`get_session` lazily creates
      the session.
    - Engine/tool side effects mutate fields directly.
    - Terminal step → :meth:`reset` (or :func:`clear_session`) wipes
      every field; the same session object is reused on the next run
      so callers holding a reference don't observe a stale instance.
    """

    # Phase 2 engine state.
    engine: Optional[Any] = None
    interaction_id: Optional[str] = None
    debug_state: Optional[Any] = None  # EngineState snapshot — observability only

    # IA-only / "both" dispatch coordination.
    pending_interact_actions: List[Any] = field(default_factory=list)
    ia_finalize_pending: bool = False

    # Cross-tool flags.
    finalized: bool = False  # set by ``response_publish(finalize=true)``
    trace_task_id: Optional[str] = None  # shared task id for engine + model task tools
    model_planned: bool = False  # True after the model called ``task_create_plan``

    # AUDIT-interact HIGH-02: per-interaction step counter that survives
    # engine rebuilds within a turn (engine's ``_iteration`` resets to
    # 0 when the stale-state guard creates a new engine). Compared
    # against ``max_iterations`` as a hard ceiling for the whole turn.
    total_steps_this_interaction: int = 0

    def reset(self) -> None:
        """Reset every field to its default (in-place — preserves identity)."""
        self.engine = None
        self.interaction_id = None
        self.debug_state = None
        self.pending_interact_actions = []
        self.ia_finalize_pending = False
        self.finalized = False
        self.trace_task_id = None
        self.model_planned = False
        self.total_steps_this_interaction = 0


def _ensure_skill_state(visitor: Any) -> Optional[dict]:
    """Return ``visitor._skill_state`` as a dict, creating it when absent."""
    if visitor is None:
        return None
    existing = getattr(visitor, "_skill_state", None)
    if isinstance(existing, dict):
        return existing
    fresh: Dict[str, Any] = {}
    try:
        visitor._skill_state = fresh
    except Exception:
        return None
    return fresh


def get_session(visitor: Any) -> EngineSession:
    """Return the visitor's ``EngineSession``, creating one on first access.

    Stable identity: subsequent calls within the same visitor run return
    the same instance, so callers can keep a reference.
    """
    state = _ensure_skill_state(visitor)
    if state is None:
        # Defensive — visitor with no mutable state bag. Return a detached
        # session so callers don't have to special-case None; mutations
        # won't survive but no crash.
        return EngineSession()
    sess = state.get(SESSION_KEY)
    if not isinstance(sess, EngineSession):
        sess = EngineSession()
        state[SESSION_KEY] = sess
    return sess


def get_session_optional(visitor: Any) -> Optional[EngineSession]:
    """Return the visitor's ``EngineSession`` if one exists, else None.

    Use this in code paths that want to read state without forcing
    creation (e.g. observability hooks, error handlers).
    """
    if visitor is None:
        return None
    state = getattr(visitor, "_skill_state", None)
    if not isinstance(state, dict):
        return None
    sess = state.get(SESSION_KEY)
    return sess if isinstance(sess, EngineSession) else None


def clear_session(visitor: Any) -> None:
    """Reset every engine-owned field to its default.

    Equivalent to popping all eight legacy keys at once. The session
    object stays attached to ``_skill_state`` (identity preserved) so
    any reference held by the engine or tools sees the cleared values.
    """
    sess = get_session_optional(visitor)
    if sess is not None:
        sess.reset()


__all__ = [
    "SESSION_KEY",
    "EngineSession",
    "get_session",
    "get_session_optional",
    "clear_session",
]
