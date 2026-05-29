"""IACenter — anchored rails-IA authority (ADR-0010 §2.1, M6).

The single home for running rails ``InteractAction`` pathways (it collapses
ADR-0009's "six homes"). Given an activation, it resolves the target IA (named
in the brief by the reflex/Executive, or matched from the registry's anchors),
AC-gates it, runs it, finalises any directives it left, and reports turn-lock as
*sustained activation* so working memory resumes it next turn.

Rails IAs own their own output (they publish to the bus or leave directives for
the persona). So the IA center returns an empty :class:`Result` — the hardened
pathway has already delivered. It makes **no** cognitive model call of its own
(``ctx.use_model`` is never called); persona rendering of leftover directives is
egress, mirroring the Persona center's ``voice``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.executive.access import (
    ExecutiveAccessDenied,
    check_delegate_access,
)
from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.contracts import RETURN, CenterDirective, Result

if TYPE_CHECKING:
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import Frame

logger = logging.getLogger(__name__)


class IACenter(BaseCenter):
    """Runs anchored rails InteractActions; reports turn-lock as sustained activation."""

    description: str = attribute(
        default="IA center — runs hardened, anchored rails interact-action pathways.",
    )
    latency_class: str = attribute(default="quick")

    not_found_text: str = attribute(
        default="I couldn't find a matching flow for that.",
    )
    access_denied_text: str = attribute(
        default="Sorry, I can't do that here.",
    )
    error_text: str = attribute(
        default="Something went wrong running that.",
    )

    def center_name(self) -> str:
        return "IACenter"

    async def tick(
        self,
        ctx: "TurnContext",
        frame: "Frame",
    ) -> CenterDirective:
        ia_name = self._target_ia_name(ctx, frame)
        if not ia_name:
            return RETURN(Result(content=self.not_found_text, verbatim=True))

        # AccessControl on the rails IA run.
        try:
            await check_delegate_access(
                ctx.agent,
                action_name=ia_name,
                user_id=getattr(ctx.visitor, "user_id", None),
                channel=getattr(ctx.visitor, "channel", "default") or "default",
            )
        except ExecutiveAccessDenied as denied:
            logger.info("IACenter: %s denied by AC", denied.resource)
            return RETURN(Result(content=self.access_denied_text, verbatim=True))

        action = await self._resolve_ia(ia_name)
        if action is None:
            logger.warning("IACenter: IA %r not resolvable", ia_name)
            return RETURN(Result(content=self.not_found_text, verbatim=True))

        try:
            await action.execute(ctx.visitor)
        except Exception as exc:
            logger.exception("IACenter: IA %r raised during execute: %s", ia_name, exc)
            return RETURN(Result(content=self.error_text, verbatim=True))

        # If the IA left directives unrendered (and didn't publish), render them
        # through the persona — rails IAs predate the persona-center egress and
        # use the directive pattern. This is egress, not a cognitive model call.
        await self._finalize_directives(ctx)

        sustain = await self._still_locking(action, ctx.visitor)
        # Empty result: the rails IA already owns its output channel.
        return RETURN(Result(content="", verbatim=True), sustain=sustain)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _target_ia_name(self, ctx: "TurnContext", frame: "Frame") -> str:
        """Resolve which IA to run: brief slot first, else a registry anchor match."""
        if frame.brief and frame.brief.slots:
            cap = frame.brief.slots.get("ia") or frame.brief.slots.get("capability")
            if cap:
                return str(cap)
        registry = getattr(ctx, "registry", None)
        if registry is not None:
            match = registry.match_anchor(ctx.utterance)
            if match is not None:
                return str(match.handle or match.id)
        return ""

    async def _resolve_ia(self, name: str) -> Optional[Any]:
        try:
            return await self.get_action(name)
        except Exception as exc:
            logger.warning("IACenter: get_action(%r) raised: %s", name, exc)
            return None

    async def _still_locking(self, action: Any, visitor: Any) -> bool:
        """True iff the IA declares turn-lock AND still owns it after running."""
        try:
            manifest = action.get_manifest()
        except Exception:
            manifest = None
        if not manifest or not getattr(manifest, "turn_lock", False):
            return False
        method = getattr(action, "is_actively_locking_turn", None)
        if method is None:
            return True  # turn_lock with no lifecycle hook → assume still locking
        try:
            result = method(visitor)
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception as exc:
            logger.debug("IACenter: is_actively_locking_turn raised: %s", exc)
            return True

    async def _finalize_directives(self, ctx: "TurnContext") -> None:
        interaction = ctx.interaction
        if interaction is None:
            return
        if getattr(interaction, "response", None):
            return  # IA published directly
        directives = getattr(interaction, "directives", None) or []
        if not directives:
            return
        try:
            await self.respond(ctx.visitor)
        except Exception as exc:
            logger.debug("IACenter: persona finalize failed: %s", exc)


__all__ = ["IACenter"]
