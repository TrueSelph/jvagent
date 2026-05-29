"""Per-turn state for the Executive pattern (ADR-0010 §2.1–2.3).

Working memory is the **authoritative turn state**. It holds the activation
stack (depth-1 star: an Executive base + at most one active center frame),
the results centers have deposited, the activation trace, and the carrier for
*sustained activation* (turn-lock) across turns.

This lives on ``visitor._executive_wm`` and is constructed fresh on the first
Executive visit per turn (rehydrated from the conversation when a sustained
activation persisted from a prior turn).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jvagent.action.executive.contracts import Brief, Result

# Default ceiling on center activations + executive ticks per turn. Replaces
# Bridge's shift-budget + jvspatial's max_visits_per_node. Generous enough for
# multi-center integration, tight enough to bound a runaway loop.
DEFAULT_ACTIVATION_BUDGET = 16


class ModelBudgetExceeded(RuntimeError):
    """Raised when an actor attempts a second model call within one tick.

    Enforces ADR-0010 invariant 1 ("one model call per tick") mechanically:
    the loop wraps each tick with a fresh :class:`ModelBudget`; the second
    ``acquire()`` raises and the loop aborts that tick rather than letting an
    actor smuggle multiple model calls into a single scheduler step.
    """


@dataclass
class ModelBudget:
    """One-shot model-call budget for a single tick."""

    max_calls: int = 1
    used: int = 0

    def acquire(self) -> None:
        if self.used >= self.max_calls:
            raise ModelBudgetExceeded(
                f"tick exceeded model-call budget (max={self.max_calls})"
            )
        self.used += 1


@dataclass
class Frame:
    """One entry on the activation stack.

    ``actor`` is ``"executive"`` or a center name. ``brief`` is the task the
    Executive handed a center (``None`` for the Executive's own frame).
    ``scratch`` is the center's private per-turn working state — the analogue
    of Bridge's ``helm_states[name]`` slot, but frame-local so concurrent
    turns on shared center singletons cannot cross-pollinate.
    """

    actor: str
    brief: Optional[Brief] = None
    on_done: str = "integrate"
    scratch: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivationEvent:
    """One activation-trace entry (observability; ADR-0010 §3 ``record_tick``)."""

    actor: str
    verb: str
    detail: str
    at_monotonic: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor": self.actor,
            "verb": self.verb,
            "detail": self.detail,
            "at_monotonic": self.at_monotonic,
        }


@dataclass
class WorkingMemory:
    """The Executive's per-turn integration buffer + activation stack."""

    stack: List[Frame] = field(default_factory=list)
    results: List[Result] = field(default_factory=list)
    trace: List[ActivationEvent] = field(default_factory=list)
    turn_started_at: float = field(default_factory=time.monotonic)
    activation_count: int = 0
    finalized: bool = False
    # Sustained activation (turn-lock) carried across turns. When set, the
    # reflex path resumes the named center on the next turn without consulting
    # the Executive. Shape: ``{"center": str, "brief": {...}}`` (JSON-safe).
    suspended: Optional[Dict[str, Any]] = None

    # -- stack helpers -------------------------------------------------

    @property
    def current(self) -> Optional[Frame]:
        return self.stack[-1] if self.stack else None

    def push(self, frame: Frame) -> None:
        self.stack.append(frame)

    def pop(self) -> Optional[Frame]:
        return self.stack.pop() if self.stack else None

    # -- trace ---------------------------------------------------------

    def record(self, actor: str, verb: str, detail: str = "") -> None:
        self.trace.append(
            ActivationEvent(
                actor=actor,
                verb=verb,
                detail=detail,
                at_monotonic=time.monotonic(),
            )
        )

    def to_observability(self) -> Dict[str, Any]:
        return {
            "trace": [e.to_dict() for e in self.trace],
            "activation_count": self.activation_count,
            "turn_started_at": self.turn_started_at,
            "result_count": len(self.results),
            "suspended": self.suspended,
        }


__all__ = [
    "DEFAULT_ACTIVATION_BUDGET",
    "ModelBudget",
    "ModelBudgetExceeded",
    "Frame",
    "ActivationEvent",
    "WorkingMemory",
]
