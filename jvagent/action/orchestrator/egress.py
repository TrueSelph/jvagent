"""Post-loop egress for OrchestratorInteractAction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class OrchestratorEgressMixin:
    @staticmethod
    def _ia_emitted(interaction: Any) -> bool:
        """True if a dispatched IA produced user-facing output this turn.

        An IA emits either by setting ``interaction.response`` OR by queuing a
        directive (the directive-based publishing pattern, rendered by
        ``_egress`` after the loop). The locked path uses this so it doesn't
        mistake directive-based publishing for silence and echo the IA-as-tool
        status sentinel.
        """
        if interaction is None:
            return False
        if (getattr(interaction, "response", "") or "").strip():
            return True
        try:
            return bool(interaction.get_unexecuted_directives())
        except Exception:
            return False

    async def _egress(self, visitor: "InteractWalker") -> None:
        """The single post-loop egress authority.

        Runs only when nothing was delivered during the loop (terminal
        reply/respond/final paths emit directly and latch ``interaction.emitted``).
        Renders any queued rails-IA directives once, then falls back to
        ``clarify_text`` — all gated by the emitted latch so the turn never
        double-sends.
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None or interaction.has_emitted():
            return
        # Gather any directives a rails IA queued this turn (no model text to add).
        await self._send_reply(visitor)
        if not interaction.has_emitted():
            await self._send_reply(visitor, self.clarify_text)

    async def _send_reply(
        self, visitor: "InteractWalker", text: str = "", *, compose: bool = False
    ) -> None:
        """Producer egress (ADR-0025). Queue reply as directive; ReplyAction gathers.

        Directives may carry model-only guidance after U+2063 (paraphrase rules,
        ``ctx.say(..., hint=...)``). When ``compose=True``, keep that guidance on
        the queued directive so ReplyAction can steer the compose model — it never
        relays post-marker text to the user. When ``compose=False`` (or falling
        through to thin publish), strip after the marker so guidance cannot leak.
        """
        interaction = getattr(visitor, "interaction", None)
        raw = (text or "").strip()
        user_facing = raw.split("\u2063", 1)[0].strip() if raw else ""
        # Compose needs the full directive (intent + model guidance/hints).
        # Literal paths queue only the user-facing portion.
        to_queue = raw if (compose and raw) else user_facing
        if interaction is not None and to_queue:
            framed = (
                to_queue
                if to_queue.lower().startswith("tell the user")
                else f"Tell the user or ask the user: {to_queue}"
            )
            try:
                interaction.add_directive(framed, self.get_class_name())
            except Exception:
                pass
        responder = await self.get_responder()
        if compose and responder is not None:
            respond = getattr(responder, "respond", None)
            if callable(respond):
                try:
                    await respond(interaction, visitor=visitor)
                    return
                except Exception as exc:
                    logger.warning("orchestrator: responder.respond failed: %s", exc)
        gather = getattr(responder, "gather", None) if responder is not None else None
        if callable(gather):
            try:
                gathered = await gather(visitor)
                if gathered:
                    return
                if interaction is not None and interaction.has_emitted():
                    return
            except Exception as exc:
                logger.warning("orchestrator: responder.gather failed: %s", exc)
        if (
            responder is not None
            and interaction is not None
            and not user_facing
            and not compose
        ):
            from jvagent.action.reply.reply_action import ReplyAction

            has_params = bool(ReplyAction._collect_parameters(None, interaction))
            if has_params:
                respond = getattr(responder, "respond", None)
                if callable(respond):
                    try:
                        await respond(interaction, visitor=visitor)
                        return
                    except Exception as exc:
                        logger.warning(
                            "orchestrator: responder.respond failed: %s", exc
                        )
        if user_facing:
            await self.publish(visitor=visitor, content=user_facing)

    async def _emit_reply(self, visitor: "InteractWalker", text: str) -> None:
        if not (text or "").strip():
            return
        await self._send_reply(visitor, text)

    async def _maybe_emit_final(self, visitor: "InteractWalker", answer: str) -> None:
        answer = (answer or "").strip()
        if not answer:
            return
        interaction = getattr(visitor, "interaction", None)
        current = (
            (getattr(interaction, "response", "") or "")
            if interaction is not None
            else ""
        )
        if answer in current:
            return
        await self._emit_reply(visitor, answer)
