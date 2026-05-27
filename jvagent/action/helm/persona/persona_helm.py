"""``PersonaHelm`` — polish-and-deliver helm (BRIDGE-ROADMAP §G, ADR-0007 v0).

Wraps :class:`PersonaAction`. Other helms SHIFT into PersonaHelm with a
``handoff_state`` carrying the draft they want polished. PersonaHelm
reads the draft, optionally adds directives, then calls
``self.respond(visitor)`` (inherited from :class:`BaseHelm`) which routes
through :class:`PersonaAction.respond` and publishes the result. Returns
``YIELD`` after persona publishes (persona handles its own response-bus
emit).

Handoff-state shape (all keys optional):

  {
    "text":      "draft text to deliver",
    "directive": "additional persona directive",
    "directives": ["multiple directives"],
    "history_limit": 3,
    "use_history": true,
  }

When ``text`` is present, PersonaHelm injects a "Tell the user: …"
directive so persona renders the draft in the agent's voice. When
``text`` is absent, persona falls back to whatever directives the
interaction already carries (set upstream by ReasoningHelm or rails
IAs).

Latency target: quick. One PersonaAction.respond() call per visit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import EMIT, YIELD, HelmStepResult

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class PersonaHelm(BaseHelm):
    """Polish-and-deliver helm orchestrated by ``BridgeInteractAction``.

    Configuration (override in ``agent.yaml.context:``):

    - ``history_limit``: turns of context PersonaAction.respond should
      include (default 3, matches PersonaAction's own default).
    - ``use_history``: whether to include history in the persona prompt
      (default True).
    - ``fallback_text``: emitted when PersonaAction is unavailable
      (no PersonaAction installed, or PersonaAction.respond raises).
    """

    description: str = attribute(
        default=(
            "Polish-and-deliver helm: wraps PersonaAction for final-response "
            "rendering after another helm prepares a draft."
        )
    )
    latency_class: str = attribute(default="quick")
    can_emit_directly: bool = attribute(default=True)

    history_limit: int = attribute(default=3)
    use_history: bool = attribute(default=True)
    # Last-resort text when PersonaAction is unavailable or raises. STATIC
    # English default — fires on rare error paths only. Operators
    # localising per language should override in ``agent.yaml.context:``.
    fallback_text: str = attribute(
        default="(Persona delivery unavailable — please try again.)"
    )

    async def _step_impl(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Render the final response via PersonaAction, then yield.

        Called by :meth:`BaseHelm.step` (the wrapper handles the
        action-trace self-recording via
        ``interaction.record_action_execution``).
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            logger.warning("PersonaHelm: visitor has no interaction; yielding")
            return YIELD()

        handoff = self._read_handoff(bridge_state)
        text = (handoff.get("text") or "").strip() if handoff else ""
        directive = (handoff.get("directive") or "").strip() if handoff else ""
        directives_extra: List[str] = (
            list(handoff.get("directives") or []) if handoff else []
        )
        if handoff and handoff.get("history_limit") is not None:
            history_limit = int(handoff.get("history_limit"))
        else:
            history_limit = int(self.history_limit)

        if handoff and "use_history" in handoff:
            use_history = bool(handoff.get("use_history"))
        else:
            use_history = bool(self.use_history)

        # Inject the draft as a persona directive so the model renders it
        # in voice rather than treating it as raw user input.
        if text:
            try:
                await visitor.add_directive(f"Tell the user: {text}")
            except Exception as exc:
                logger.debug("PersonaHelm: failed to add draft directive: %s", exc)
        if directive:
            try:
                await visitor.add_directive(directive)
            except Exception as exc:
                logger.debug("PersonaHelm: failed to add explicit directive: %s", exc)

        all_directives: List[str] = []
        if directives_extra:
            all_directives.extend(d for d in directives_extra if d.strip())

        try:
            response = await self.respond(
                visitor,
                directives=all_directives or None,
                use_history=use_history,
                history_limit=max(1, history_limit),
            )
        except Exception as exc:
            logger.warning("PersonaHelm: respond raised: %s", exc, exc_info=True)
            return self._safe_fallback()

        if response is None:
            # PersonaAction not found or returned None — emit fallback
            # so the turn isn't silently empty.
            return self._safe_fallback()

        # PersonaAction.respond publishes the response itself; YIELD
        # cleanly so Bridge clears state and the walker exits.
        try:
            interaction.set_to_executed()
        except Exception:
            pass
        return YIELD()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_handoff(self, bridge_state: "BridgeState") -> Optional[Dict[str, Any]]:
        """Return the handoff_state slot for this helm, or None."""
        slot = bridge_state.helm_states.get(self.helm_name())
        if isinstance(slot, dict):
            return slot
        return None

    def _safe_fallback(self) -> HelmStepResult:
        """Emit a brief acknowledgement when persona delivery fails."""
        text = self.fallback_text or "(no response)"
        logger.warning("PersonaHelm: emitting fallback text")
        return EMIT(text=text, finalize=True)
