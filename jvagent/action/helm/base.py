"""BaseHelm — abstract Action subclass orchestrated by ``BridgeInteractAction``.

A helm is **not** an ``InteractAction``: the walker does not visit it directly.
Bridge visits the helm via ``await helm.step(visitor, bridge_state)`` exactly
once per Bridge walker visit, then dispatches the returned ``HelmStepResult``
verb.

Subclasses MUST implement :meth:`step` and SHOULD declare a manifest in their
package ``info.yaml`` (loader integration arrives at milestone D).

Each helm's :meth:`step` issues **at most one** model call. This invariant
(ADR-0002 / ADR-0007) is load-bearing across cockpit and bridge.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.helm.contracts import HelmStepResult

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class BaseHelm(Action):
    """Abstract base for all helms orchestrated by Bridge.

    Subclasses inherit the standard ``Action`` lifecycle (``on_register``,
    ``on_enable``, ``on_startup``, ``on_disable``, ``on_deregister``). Helms
    contribute neither cockpit tools nor capabilities by default; override
    :meth:`get_tools` / :meth:`get_capabilities` if a helm needs to expose
    either (uncommon — helms are orchestrated by Bridge, not by another helm).

    The ``latency_class`` attribute is a convenience mirror of the manifest
    field. It is consulted by Bridge when deciding whether to publish an
    ``ack-on-shift`` before a ``SHIFT`` (milestone E wires this from the
    manifest; B reads it from this attribute as a fallback).
    """

    description: str = attribute(
        default="Abstract helm — subclasses override step() to produce a HelmStepResult.",
        description="Helm description (subclasses should override).",
    )
    latency_class: str = attribute(
        default="quick",
        description=(
            "One of: instant | quick | deliberate | long. Used by Bridge for "
            "ack-on-shift gating. Mirrors the manifest field; manifest takes "
            "precedence when present."
        ),
    )
    can_interrupt: bool = attribute(
        default=False,
        description=(
            "True iff this helm is allowed to emit SHIFT(interrupt=True). "
            "Defaults False; set True on Reflex-class helms."
        ),
    )
    can_emit_directly: bool = attribute(
        default=True,
        description=(
            "False forces this helm to never EMIT (it must SHIFT or DELEGATE). "
            "Used by classifier-style helms at milestone E."
        ),
    )

    @abstractmethod
    async def step(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Execute one helm step and return a single verb.

        Implementations:

        - MUST issue at most one language-model call per invocation.
        - MUST NOT mutate ``bridge_state.gear_trace`` / ``shift_count`` —
          Bridge owns those fields.
        - MAY read and write per-helm state via
          ``bridge_state.helm_states.setdefault(self.helm_name(), {})``.
        - MAY publish thoughts/progress via ``visitor.publish(...)`` for
          observability but MUST emit user-facing text only through an
          ``EMIT`` verb.

        Returns:
            One of :class:`EMIT`, :class:`EXECUTE`, :class:`SHIFT`,
            :class:`DELEGATE`, :class:`YIELD`.
        """

    def helm_name(self) -> str:
        """Stable identifier used for ``BridgeState.current_helm`` and AC labels.

        Defaults to the class name (e.g. ``"ReflexHelm"``). Subclasses MAY
        override to provide a shorter slug, but the AC resource label
        ``tool:helm:{helm_name}`` MUST match whatever this returns.
        """
        return self.__class__.__name__

    # ------------------------------------------------------------------
    # Publishing helpers
    #
    # Lifted verbatim from :class:`jvagent.action.interact.base.InteractAction`
    # so helms can call ``self.publish(...)`` / ``self.publish_thought(...)``
    # the same way cockpit-style InteractActions do. Helms are not
    # InteractActions; they need their own copy because Action does not
    # provide these methods.
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
        """Publish a response directly to the response bus.

        Mirrors :meth:`InteractAction.publish` so duplicated cockpit code
        (``deliver_final_response``, ``deliver_conversational``,
        ``deliver_via_persona``) that calls ``action.publish(...)`` works
        unchanged when ``action`` is a helm.
        """
        if not content and not allow_empty:
            logger.error("BaseHelm.publish: content is required")
            return None

        if not visitor.response_bus:
            logger.warning(
                "ResponseBus not available — cannot publish response. "
                "Ensure InteractWalker has response_bus initialized."
            )
            return None

        if not visitor.session_id:
            logger.warning("Session ID not available — cannot publish response")
            return None

        interaction = visitor.interaction
        if not interaction:
            logger.warning(
                "Interaction not available — cannot publish response or set "
                "interaction.response"
            )
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
            user_id=interaction.user_id if hasattr(interaction, "user_id") else None,
            metadata=pub_metadata,
            streaming_complete=streaming_complete,
            transient=transient,
            category=category,
            thought_type=thought_type,
            segment_id=segment_id,
            relay_to_adapters=relay_to_adapters,
        )

    async def publish_thought(
        self,
        visitor: "InteractWalker",
        content: str,
        *,
        thought_type: str = "reasoning",
        segment_id: Optional[str] = None,
        streaming_complete: bool = True,
        relay_to_adapters: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
        stream: Optional[bool] = None,
        allow_empty: bool = False,
    ) -> Optional[Any]:
        """Publish a thought-category message with helm-level relay defaults."""
        relay_default = bool(getattr(self, "relay_thoughts_to_channels", False))
        return await self.publish(
            visitor=visitor,
            content=content,
            metadata=metadata,
            streaming_complete=streaming_complete,
            stream=stream,
            transient=True,
            category="thought",
            thought_type=thought_type,
            segment_id=segment_id,
            relay_to_adapters=(
                relay_default if relay_to_adapters is None else relay_to_adapters
            ),
            allow_empty=allow_empty,
        )

    async def respond(
        self,
        visitor: "InteractWalker",
        directives=None,
        parameters=None,
        *,
        use_history: bool = True,
        history_limit: int = 3,
        with_utterance: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
        transient: bool = False,
    ) -> Optional[str]:
        """Generate a response via PersonaAction.

        Lifted verbatim from :meth:`InteractAction.respond` so duplicated
        cockpit delivery helpers (``deliver_via_persona``,
        ``deliver_conversational``, ``deliver_final_response``) work
        unchanged when ``action`` is a helm.

        Returns the generated response string, or None if PersonaAction is
        not found or generation errored. PersonaAction.respond writes
        ``interaction.response`` itself; this method does not duplicate
        that write.
        """
        interaction = visitor.interaction
        if not interaction:
            logger.error("BaseHelm.respond: No interaction available in visitor")
            return None

        try:
            if directives:
                await visitor.add_directives(directives)

            if parameters:
                action_name = getattr(
                    self, "get_class_name", lambda: self.__class__.__name__
                )()
                if interaction.add_parameters(parameters, action_name):
                    await interaction.save()

            from jvagent.action.persona.persona_action import PersonaAction

            persona = await self.get_action(PersonaAction)
            if not persona:
                logger.debug(
                    "BaseHelm.respond: PersonaAction not found; skipping response"
                )
                return None

            response = await persona.respond(
                interaction,
                visitor=visitor,
                use_history=use_history,
                history_limit=history_limit,
                with_utterance=with_utterance,
                with_interpretation=with_interpretation,
                with_event=with_event,
                with_response=with_response,
                max_statement_length=max_statement_length,
                transient=transient,
            )
            return response
        except Exception as exc:
            logger.error(
                "BaseHelm.respond: error calling PersonaAction: %s",
                exc,
                exc_info=True,
            )
            return None
