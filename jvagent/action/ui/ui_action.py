"""UiAction / UiInteractAction — let the model render UI components.

The embeddable messenger owns a fixed component catalog and renders whatever
arrives on ``metadata.ui``. This is the server half: a model-callable
``ui__render`` tool, plus the flush that actually publishes.

**Why two pieces.** A tool cannot emit UI by returning it — ``ToolResult``
forwards only ``content`` to the client, never its metadata. And publishing from
inside the tool would put the component on the wire *during* the loop, i.e.
**before** the assistant's sentence, which is the wrong reading order for every
component in the catalog. So ``ui__render`` only *stages* an envelope, and
:class:`UiInteractAction` (weight 90 — after the Orchestrator, before
Suggestions at 100) flushes it once the reply exists.

**Why this is safe under ADR-0024.** The flush publishes an *empty*
``category:"user"`` message carrying only metadata. ``mark_emitted`` is gated on
non-empty content, so the latch is never tripped and ``interaction.response`` is
untouched — the turn still has exactly one text egress. This is the same shape
``SuggestionsInteractAction`` already uses.

**Thin harness.** ``ui__render`` is model-invoked and domain-agnostic: routing is
tool selection. What would violate the contract is a *server-side* rule that
inspects the utterance or reply and decides to render something — that judgment
belongs to the model or a skill SOP.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.tooling.tool_decorator import collect_tools, tool

logger = logging.getLogger(__name__)

#: Components the messenger can render. Anything else is refused at the tool
#: boundary so a typo degrades to plain text instead of a silent no-op.
UI_CATALOG = ("card", "choices")

#: Envelope version the client understands.
UI_ENVELOPE_VERSION = 1

#: Ceiling on a serialized envelope, so a runaway tool call can't blow an SSE
#: frame or the session queue.
MAX_ENVELOPE_CHARS = 32_000

#: Walker attribute holding envelopes staged during the loop. Deliberately *not*
#: ``visitor.data`` — that dict is merged into the metadata of **every**
#: published message (``interact/base.py``), so staging there would leak the
#: envelope onto every frame of the turn.
_PENDING_ATTR = "_jvagent_pending_ui"


def _pending(visitor: Any) -> List[Dict[str, Any]]:
    items = getattr(visitor, _PENDING_ATTR, None)
    if not isinstance(items, list):
        items = []
        setattr(visitor, _PENDING_ATTR, items)
    return items


def build_envelope(
    component: str,
    props: Optional[Dict[str, Any]],
    fallback: str,
    envelope_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble a validated envelope, or raise ``ValueError`` with the reason."""
    comp = (component or "").strip().lower()
    if comp not in UI_CATALOG:
        raise ValueError(
            f"unknown component {component!r}; choose one of {', '.join(UI_CATALOG)}"
        )
    if not isinstance(props, dict):
        raise ValueError("props must be an object")
    # Reject an empty shell: without real props the component renders as its
    # fallback text, which is indistinguishable from just replying. Naming the
    # missing key lets the model retry correctly instead of silently degrading.
    if comp == "choices" and not props.get("options"):
        raise ValueError(
            "choices requires props.options: [{label, value, description?}]"
        )
    if comp == "card" and not any(
        props.get(k) for k in ("title", "body", "fields", "image")
    ):
        raise ValueError("card requires at least props.title, body, fields or image")
    text = (fallback or "").strip()
    if not text:
        # Without this the component is invisible on non-web channels, in the
        # transcript, and to screen readers.
        raise ValueError("fallback text is required")

    env = {
        "v": UI_ENVELOPE_VERSION,
        "component": comp,
        "id": envelope_id or f"ui_{uuid.uuid4().hex[:10]}",
        "props": props,
        "fallback": text,
    }
    if len(str(env)) > MAX_ENVELOPE_CHARS:
        raise ValueError("component payload is too large")
    return env


class UiAction(InteractAction):
    """Publishes ``ui__render`` **and** flushes what it stages.

    One class rather than two: an action package declares a single ``archetype``,
    so a separate flusher class in this module would never be instantiated by the
    loader. ``InteractAction`` extends ``Action``, so the ``@tool`` surface works
    exactly the same while ``execute()`` gives us the post-reply flush.
    """

    description: str = attribute(
        default="Renders a UI component (card, choices) in the chat.",
        description="Action description",
    )

    weight: int = attribute(
        default=90,
        description=(
            "After the Orchestrator (-200) so the reply exists, before "
            "Suggestions (100)."
        ),
    )

    always_execute: bool = attribute(
        default=True,
        description="Always check for staged components.",
    )

    max_per_turn: int = attribute(
        default=2,
        description="Cap on components rendered in a single turn.",
    )

    async def get_tools(self) -> List[Any]:
        """Publish the decorated ``ui__render`` tool.

        ``InteractAction.get_tools()`` returns nothing for an ``always_execute``
        action — that path exists to expose *routable* IAs as intent tools, which
        is not what this is. We need both halves: the tool on the surface, and an
        ``execute()`` that always runs to flush. So collect the decorated methods
        directly instead of inheriting the routing behaviour.
        """
        return collect_tools(self)

    @tool(name="ui__render")
    async def render(
        self,
        component: Annotated[str, "Component to render: 'card' or 'choices'."],
        fallback: Annotated[
            str,
            "One-line plain-text version of the component. Required — it is what "
            "non-web channels, the transcript and screen readers get.",
        ],
        props: Annotated[
            Optional[Dict[str, Any]],
            "Component data. card: title, subtitle, body, image{url,alt}, "
            "fields[{label,value}], actions[{label,kind:'send'|'link',value|href}]. "
            "choices: prompt, options[{label,value,description,disabled}].",
        ] = None,
        **kwargs: Any,
    ) -> Any:
        """Render a UI component beneath your reply. Use for structured results (an order, a comparison) or a small set of choices — not for ordinary prose. At most one per turn; add a short framing sentence and do not repeat the component's contents."""  # noqa: E501
        from jvagent.tooling.tool_executor import get_tool_visitor
        from jvagent.tooling.tool_result import ToolResult

        visitor = kwargs.get("visitor") or get_tool_visitor()
        if visitor is None:
            return ToolResult(content="not_rendered: no active turn", is_error=True)

        # Components only exist on the streaming web surface. Publishing an empty
        # message to a non-streaming channel adapter would post a blank message.
        if not getattr(visitor, "stream", False):
            return ToolResult(
                content=(
                    "not_rendered: this channel cannot display components — "
                    "state the information in your reply text instead."
                )
            )

        try:
            env = build_envelope(component, props or {}, fallback)
        except ValueError as exc:
            return ToolResult(content=f"not_rendered: {exc}", is_error=True)

        staged = _pending(visitor)
        if len(staged) >= max(1, self.max_per_turn):
            return ToolResult(
                content="not_rendered: component limit for this turn reached"
            )
        staged.append(env)

        return ToolResult(
            content=(
                f"staged {env['component']} (id={env['id']}); it will render under "
                "your reply. Do not repeat its contents — add at most one short "
                "framing sentence."
            )
        )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Publish each staged envelope as an empty metadata-only message."""
        interaction = visitor.interaction
        staged = getattr(visitor, _PENDING_ATTR, None)
        if not interaction or not staged:
            await visitor.unrecord_action_execution()
            return
        if not getattr(visitor, "stream", False):
            await visitor.unrecord_action_execution()
            return

        try:
            for env in staged:
                await self.publish(
                    visitor,
                    content="",
                    allow_empty=True,
                    category="user",
                    metadata={"ui": env},
                    stream=False,
                )
            setattr(visitor, _PENDING_ATTR, [])
        except Exception as exc:  # a component must never break the turn
            logger.error("UiInteractAction: %s", exc, exc_info=True)
            await visitor.unrecord_action_execution()

    async def healthcheck(self) -> Union[bool, dict]:
        return True
