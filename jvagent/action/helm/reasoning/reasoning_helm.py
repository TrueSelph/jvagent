"""``ReasoningHelm`` — Bridge helm running the cockpit-style engine loop.

Pattern source (duplicated, not imported): ``jvagent/action/cockpit/cockpit_interact_action.py``
at commit ``3cd4ebb`` (head of dev-cockpit-audit before Bridge work began).
Subsequent commits in ``jvagent/action/cockpit/`` may diverge — this helm is
self-contained.

At C-1 the helm ships as a skeleton: a single ``EMIT(finalize=True)`` is
returned per visit, so Bridge + ReasoningHelm produces a one-shot response
without any real LM call. C-2 wires the duplicated engine.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jvspatial.core.annotations import attribute

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import EMIT, HelmStepResult

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


# Default model + provider mirror the cockpit defaults so the Bridge smoke
# harness compares apples-to-apples against baseline ``7d95904``.
DEFAULT_REASONING_MODEL = "claude-sonnet-4-20250514"
DEFAULT_REASONING_MODEL_ACTION = "AnthropicLanguageModelAction"


class ReasoningHelm(BaseHelm):
    """Deliberate-class reasoning helm orchestrated by ``BridgeInteractAction``.

    Each ``step()`` performs **at most one** model call (ADR-0002 invariant).
    The helm dispatches tools internally (cockpit-style) and signals
    ``CONTINUE`` to request another walker visit; final user-facing text is
    rendered through :class:`PersonaAction` and returned via ``EMIT``.

    C-1 ships a placeholder ``step()`` returning ``EMIT(finalize=True)`` with
    a static message so Bridge + ReasoningHelm can be wired end-to-end without
    real model traffic.
    """

    description: str = attribute(
        default=(
            "Deliberate-class reasoning helm: think/act/observe loop with "
            "full harness + action tool agency. Calls PersonaAction directly "
            "for final delivery."
        )
    )
    latency_class: str = attribute(default="deliberate")
    can_emit_directly: bool = attribute(default=True)
    can_interrupt: bool = attribute(default=False)

    # --- C-2 will add the full attribute surface (model, max_iterations,
    # max_duration_seconds, reasoning_*, stuck_*, tool_tier, prompts, etc.)
    # by duplicating the cockpit declarations. C-1 keeps the attribute
    # surface minimal so the skeleton is reviewable in isolation.
    model: str = attribute(default=DEFAULT_REASONING_MODEL)
    model_action_type: str = attribute(default=DEFAULT_REASONING_MODEL_ACTION)

    async def step(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """C-1 placeholder. Returns an EMIT so the helm is exercisable end-to-end.

        Subsequent sub-milestones replace the body:

        - C-2 wires the duplicated engine and tool dispatch loop.
        - C-3 plugs in harness service tools.
        - C-4 plugs in routing (Phase 1).
        - C-5 plugs in skill catalog.
        - C-6 plugs in persona delivery.
        """
        utterance = (getattr(visitor, "utterance", None) or "").strip()
        body = utterance or "(empty utterance)"
        return EMIT(
            text=f"[ReasoningHelm placeholder] received: {body}",
            finalize=True,
        )
