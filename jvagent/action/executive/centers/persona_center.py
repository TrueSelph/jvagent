"""PersonaCenter — the agent's language/identity center (ADR-0010 §2.4).

The sole egress: every center's output and the Executive's own replies are
voiced through here, so identity lives in exactly one place. It wraps the
existing ``PersonaAction`` for stylisation and falls back to a raw publish when
the content is ``verbatim`` (already final-form) or no ``PersonaAction`` is
installed.

The PersonaCenter is a :class:`BaseCenter` for taxonomic consistency, but it is
the egress — it is invoked via :meth:`voice` from the Executive's ``_egress``
rather than recruited into the activation loop. Its :meth:`tick` is therefore a
defensive no-op (it should never be ``ACTIVATE``-d like a worker center).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.contracts import RETURN, CenterDirective, Result

if TYPE_CHECKING:
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import Frame
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class PersonaCenter(BaseCenter):
    """Language/identity egress. Voices all final user-facing prose."""

    description: str = attribute(
        default="Language/identity center — the sole egress that voices all output.",
    )
    latency_class: str = attribute(default="quick")

    def center_name(self) -> str:
        return "PersonaCenter"

    async def voice(
        self,
        visitor: "InteractWalker",
        *,
        content: str,
        verbatim: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Voice ``content`` to the user. Returns True iff something was published.

        - ``verbatim`` content (already final-form, e.g. a skill's literal
          output) is published raw.
        - Otherwise the content is stylised through ``PersonaAction`` so the
          agent's identity/voice is applied. If no ``PersonaAction`` is
          installed, falls back to a raw publish so the user still hears back.
        """
        text = (content or "").strip()
        if not text:
            return False

        if not verbatim:
            styled = await self.respond(visitor, directives=[f"Tell the user: {text}"])
            if styled is not None:
                return True
            # No PersonaAction (or it errored) — fall through to raw publish.

        await self.publish(visitor=visitor, content=text, metadata=meta or None)
        return True

    async def tick(
        self,
        ctx: "TurnContext",
        frame: "Frame",
    ) -> CenterDirective:
        # Defensive: the Persona center is the egress, not a worker. If it is
        # ever ACTIVATE-d, voice the brief intent (if any) and return.
        logger.debug("PersonaCenter.tick called — egress should use voice(); returning")
        content = frame.brief.intent if frame.brief else ""
        if content:
            await self.voice(ctx.visitor, content=content)
        return RETURN(Result(content="", verbatim=True))


__all__ = ["PersonaCenter"]
