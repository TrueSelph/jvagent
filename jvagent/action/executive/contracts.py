"""Contracts for the Executive + Centers pattern (ADR-0010).

A brain-shaped composition: one **Executive** (prefrontal cortex) engages
trivial conversation, knows all **centers** via a registry, holds **working
memory**, activates centers, integrates results, and decides when to respond.
Centers are specialist **leaves**: they either keep working (``STEP``) or hand
back a result (``RETURN``). Only the Executive activates centers, so the
recruitment graph is a depth-1 star — no cycles, no ping-pong.

Verb sets are **role-typed** (there is no shared 5-verb soup):

- The Executive emits one of: :class:`ACTIVATE`, :class:`RESPOND`, :class:`YIELD`.
- A center emits one of: :class:`STEP`, :class:`RETURN`.

``RESPOND`` is sugar for "hand this content to the Persona center to voice,
then end the turn". See ADR-0010 §2.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

# Visitor attribute carrying the per-turn working memory (ADR-0010 §2.1).
# Parallel to Bridge's ``_bridge_state`` / Cockpit's ``_skill_state``.
WORKING_MEMORY_VISITOR_ATTR = "_executive_wm"

# How a center's RETURN should be delivered (ADR-0010 §2.2):
#  - "integrate": result lands in working memory; the Executive is ticked
#    again to frame it or activate another center.
#  - "voice": result routes straight to the Persona center and the turn ends.
OnDone = Literal["integrate", "voice"]


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Brief:
    """A task handed from the Executive to a center.

    Deliberately small and free-text-friendly for v1 (ADR-0010 OQ #2);
    ``slots`` / ``constraints`` are present so the contract can grow without
    a breaking change.
    """

    intent: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)
    constraints: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Result:
    """What a center hands back via :class:`RETURN`.

    ``content`` is the substantive output. ``verbatim=True`` tells the Persona
    center to publish it as-is (skip restyle) — used when a center already
    produced final-form text. ``mode`` and ``meta`` are forwarded to the
    Persona delivery path.
    """

    content: str = ""
    verbatim: bool = False
    mode: str = "voice"
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Executive verbs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ACTIVATE:
    """Executive recruits a center.

    ``center`` resolves against the agent's registered centers. ``brief`` is
    the task. ``on_done`` chooses integrate-vs-voice for the center's RETURN.
    ``ack`` is an optional transient lead-in published immediately (e.g.
    "Looking that up…") when recruiting a slow center.
    """

    center: str
    brief: Brief = field(default_factory=Brief)
    on_done: OnDone = "integrate"
    ack: Optional[str] = None


@dataclass(frozen=True)
class RESPOND:
    """Executive hands ``content`` to the Persona center to voice, then ends.

    Sugar for "produce final user-facing prose via the sole egress". The
    Executive never publishes prose itself — the Persona center does.
    """

    content: str
    verbatim: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YIELD:
    """Executive cedes the turn to the rails weight chain.

    The Executive loop ends and ``execute()`` returns; the walker proceeds to
    the next weight-ordered InteractAction. No prose is produced.
    """


# ---------------------------------------------------------------------------
# Center verbs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class STEP:
    """Center did internal work this tick (tools / a rails IA) — recruit again.

    ``scratch`` is merged into the center's frame scratch for the next tick.
    Mirrors Bridge's ``CONTINUE``.
    """

    scratch: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RETURN:
    """Center is finished; deposit ``result`` and return control to the Executive.

    Whether the result is integrated (Executive ticked again) or voiced
    directly (turn ends) is decided by the ``on_done`` of the ACTIVATE that
    recruited this center.
    """

    result: Result = field(default_factory=Result)
    # When True, the center is declaring that it remains the owner of the turn
    # across user messages (turn-lock / "sustained activation"). The Executive
    # persists this in working memory; the reflex path resumes it next turn.
    sustain: bool = False


ExecutiveDirective = Union[ACTIVATE, RESPOND, YIELD]
CenterDirective = Union[STEP, RETURN]
Directive = Union[ACTIVATE, RESPOND, YIELD, STEP, RETURN]

EXECUTIVE_VERBS = (ACTIVATE, RESPOND, YIELD)
CENTER_VERBS = (STEP, RETURN)


def is_executive_directive(obj: Any) -> bool:
    return isinstance(obj, EXECUTIVE_VERBS)


def is_center_directive(obj: Any) -> bool:
    return isinstance(obj, CENTER_VERBS)


__all__ = [
    "WORKING_MEMORY_VISITOR_ATTR",
    "OnDone",
    "Brief",
    "Result",
    "ACTIVATE",
    "RESPOND",
    "YIELD",
    "STEP",
    "RETURN",
    "ExecutiveDirective",
    "CenterDirective",
    "Directive",
    "EXECUTIVE_VERBS",
    "CENTER_VERBS",
    "is_executive_directive",
    "is_center_directive",
]
