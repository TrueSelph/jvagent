"""PageContextInteractAction — surface host-page context to the model.

The embeddable messenger sends where the visitor actually is (path, title,
referrer, dwell, scroll depth, repeat visit) on ``data.page_context``. That
lands on ``visitor.data``, but **nothing surfaces ``visitor.data`` to the
model** — only ``image_urls`` are consumed, by the vision reflex. So without
this action the context is delivered and then ignored.

This action renders it as a short factual line and contributes it as a
response-shaping *parameter*, exactly as ``IntroInteractAction`` does for a
first-time visitor.

Thin-harness note: it states **facts about the visitor's situation** and stops
there. It deliberately draws no conclusions, names no tool, and suggests no
next step — that judgment belongs to the model and to skill SOPs. Adding
anything like "therefore offer a demo" here would be turn-prep steering and is
forbidden (``docs/thin-harness.md`` invariant 3).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


def format_page_context(ctx: Dict[str, Any]) -> Optional[str]:
    """Render a page-context payload as one short factual sentence.

    Returns ``None`` when there is nothing worth saying, so a turn without
    usable context contributes no parameter at all.
    """
    if not isinstance(ctx, dict):
        return None

    bits = []
    title = str(ctx.get("title") or "").strip()
    path = str(ctx.get("path") or "").strip()
    if title and path:
        bits.append(f'the page "{title}" ({path})')
    elif title:
        bits.append(f'the page "{title}"')
    elif path:
        bits.append(f"the page {path}")

    if not bits:
        return None

    where = f"The visitor is currently on {bits[0]}"

    extras = []
    seconds = ctx.get("secondsOnPage")
    if isinstance(seconds, (int, float)) and seconds >= 30:
        extras.append(f"has been there about {int(seconds)}s")
    depth = ctx.get("scrollDepth")
    if isinstance(depth, (int, float)) and depth >= 50:
        extras.append(f"has scrolled ~{int(depth)}% down")
    if ctx.get("returning"):
        visits = ctx.get("visitCount")
        if isinstance(visits, int) and visits > 1:
            extras.append(f"is a returning visitor (visit {visits})")
        else:
            extras.append("is a returning visitor")
    referrer = str(ctx.get("referrer") or "").strip()
    if referrer:
        extras.append(f"arrived from {referrer}")

    if extras:
        where += ", and " + ", ".join(extras)
    return where + "."


class PageContextInteractAction(InteractAction):
    """Contribute host-page context as a response-shaping parameter."""

    description: str = attribute(
        default="Surfaces the messenger's host-page context to the model.",
        description="Action description",
    )

    weight: int = attribute(
        default=-250,
        description=(
            "Runs before the Orchestrator (-200) so the context is on the "
            "interaction before the model composes."
        ),
    )

    always_execute: bool = attribute(
        default=True,
        description="Always inspect the turn for page context.",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Add a factual page-context line when the client supplied one."""
        interaction = visitor.interaction
        if not interaction:
            await visitor.unrecord_action_execution()
            return
        try:
            data = getattr(visitor, "data", None) or {}
            ctx = data.get("page_context")
            line = format_page_context(ctx) if ctx else None
            if not line:
                await visitor.unrecord_action_execution()
                return
            # Parameters are *conditional response rules* — the renderer emits
            # them as "When <condition>: <rule>" (see render_parameters in
            # jvagent/action/parameters.py). Supplying the context as a bare
            # unconditional blob reads as a style note and gets ignored; scoping
            # it to the case where it matters is both idiomatic and keeps this a
            # HOW (parameter), never a WHAT (directive).
            # Scope matters. Response-scoped params shape the *responder*, and
            # the Orchestrator's literal `reply` path can skip that compose
            # entirely — so the model never sees them while reasoning.
            # Orchestration scope puts the context in the agentic loop prompt,
            # which is where the model actually decides what to say.
            await visitor.add_parameter(
                {
                    "scope": "orchestration",
                    "condition": (
                        "the visitor refers to the page they are on, where they "
                        "are, or what they are looking at"
                    ),
                    "response": line,
                }
            )
        except Exception as exc:  # never break the turn over context
            logger.error("PageContextInteractAction: %s", exc, exc_info=True)
            await visitor.unrecord_action_execution()

    async def healthcheck(self) -> Union[bool, dict]:
        return True
