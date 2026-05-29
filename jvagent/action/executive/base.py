"""BaseCenter — abstract specialist leaf orchestrated by the Executive.

A center is **not** an ``InteractAction``: the walker never visits it. The
Executive recruits it via ``await center.tick(ctx, frame)`` exactly once per
activation tick, then dispatches the returned :class:`CenterDirective`.

Centers are **leaves** (ADR-0010 §3 inv. 2): they may only ``STEP`` (keep
working) or ``RETURN`` (hand back a result). A center never activates another
center — only the Executive does. Each ``tick`` issues **at most one** model
call (the loop enforces this via the per-tick :class:`ModelBudget` on ``ctx``).
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.executive.contracts import CenterDirective

if TYPE_CHECKING:
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import Frame
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class BaseCenter(Action):
    """Abstract base for all centers recruited by the Executive.

    Subclasses inherit the standard ``Action`` lifecycle. They contribute
    neither engine tools nor capabilities by default. ``latency_class`` mirrors
    the manifest field and lets the Executive decide whether to publish an
    ack before recruiting a slow center.
    """

    description: str = attribute(
        default="Abstract center — subclasses override tick() to return a CenterDirective.",
        description="Center description (subclasses should override).",
    )
    latency_class: str = attribute(
        default="quick",
        description="One of: instant | quick | deliberate | long. Mirrors the manifest.",
    )

    def center_name(self) -> str:
        """Stable identifier used for activation, AC labels, and the registry.

        Defaults to the class name. The AC resource ``tool:center:{name}`` MUST
        match whatever this returns.
        """
        return self.__class__.__name__

    @abstractmethod
    async def tick(
        self,
        ctx: "TurnContext",
        frame: "Frame",
    ) -> CenterDirective:
        """Run one activation tick and return a single verb.

        Implementations:

        - MUST issue at most one model call (acquire ``ctx.model_budget`` first).
        - MUST return :class:`STEP` (more work, recruit me again) or
          :class:`RETURN` (done; deposit a :class:`Result`).
        - MUST NOT activate another center (centers are leaves).
        - MAY read/write ``frame.scratch`` for per-turn working state.
        - MAY publish thoughts/progress via ``ctx.publish_thought`` but MUST
          NOT publish final user-facing prose — that is the Persona center's
          sole responsibility.
        """

    # ------------------------------------------------------------------
    # Publishing helpers (lifted from InteractAction / BaseHelm so centers can
    # publish). NOTE: only the Persona center should publish FINAL prose; other
    # centers use ``ctx.publish_thought`` for progress and ``RETURN`` results.
    # ------------------------------------------------------------------

    async def publish(
        self,
        visitor: "InteractWalker",
        content: str,
        channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        streaming_complete: bool = True,
        stream: Optional[bool] = None,
        transient: bool = False,
        category: str = "user",
        thought_type: Optional[str] = None,
        segment_id: Optional[str] = None,
        relay_to_adapters: bool = False,
        allow_empty: bool = False,
    ) -> Optional[Any]:
        """Publish to the response bus (mirrors ``InteractAction.publish``)."""
        if not content and not allow_empty:
            logger.error("BaseCenter.publish: content is required")
            return None
        if not getattr(visitor, "response_bus", None):
            logger.warning("BaseCenter.publish: ResponseBus not available")
            return None
        if not getattr(visitor, "session_id", None):
            logger.warning("BaseCenter.publish: session_id not available")
            return None
        interaction = getattr(visitor, "interaction", None)
        if not interaction:
            logger.warning("BaseCenter.publish: interaction not available")
            return None
        use_stream = stream if stream is not None else getattr(visitor, "stream", False)
        pub_channel = channel or visitor.channel
        visitor_data = getattr(visitor, "data", None) or {}
        pub_metadata = {**(metadata or {}), **visitor_data}
        return await visitor.response_bus.publish(
            session_id=visitor.session_id,
            content=content,
            channel=pub_channel,
            stream=use_stream,
            interaction_id=interaction.id,
            interaction=interaction,
            user_id=getattr(interaction, "user_id", None),
            metadata=pub_metadata,
            streaming_complete=streaming_complete,
            transient=transient,
            category=category,
            thought_type=thought_type,
            segment_id=segment_id,
            relay_to_adapters=relay_to_adapters,
        )

    async def respond(
        self,
        visitor: "InteractWalker",
        directives: Optional[List[str]] = None,
        *,
        use_history: bool = True,
        history_limit: int = 3,
        transient: bool = False,
    ) -> Optional[str]:
        """Generate a response via ``PersonaAction`` (mirrors ``BaseHelm.respond``).

        Returns the generated string, or ``None`` when no ``PersonaAction`` is
        installed or generation errors. ``PersonaAction.respond`` writes
        ``interaction.response`` itself.
        """
        interaction = getattr(visitor, "interaction", None)
        if not interaction:
            return None
        try:
            if directives:
                await visitor.add_directives(directives)
            from jvagent.action.persona.persona_action import PersonaAction

            persona = await self.get_action(PersonaAction)
            if not persona:
                return None
            return await persona.respond(
                interaction,
                visitor=visitor,
                use_history=use_history,
                history_limit=history_limit,
                transient=transient,
            )
        except Exception as exc:
            logger.error("BaseCenter.respond: error calling PersonaAction: %s", exc)
            return None


__all__ = ["BaseCenter"]
