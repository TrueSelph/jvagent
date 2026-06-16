"""Processor-facing directive sink for interview pre/post processors.

A pre/post processor's ``response_directive`` is a single bare instruction (one
question). When a processor also needs to surface CONTENT the user must SEE —
an options list, a table, a rendered summary — that content does NOT belong in
the ``tell_user`` ``note``: the note is model-only guidance and producer egress
strips it (everything after ``DIRECTIVE_GUIDANCE_MARKER``) before the reply
reaches the user.

This module provides the standard interface for that case. A processor declares
a ``directives`` parameter and the hook dispatcher injects an
:class:`InterviewDirectives` bound to the live interaction (the same
signature-filtered injection used for ``session`` / ``visitor``). Entries queued
through it land on ``interaction.directives`` (ADR-0025) and ReplyAction composes
them into the reply, so they survive egress alongside the bare question.

    async def get_available_training_times(directives, session=None, **kwargs):
        directives.tell_user("Here are the available slots:\n" + slots_text)
        return {"response_directive": tell_user("Which time works for you?")}

Outside a live turn (tests, terminal prep) the interaction may be absent; the
sink degrades to a no-op and reports it via :pyattr:`available`.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Directives this sink emits are attributed to the interview action, matching the
# ReplyAction composition contract (ADR-0025).
_SOURCE = "InterviewAction"

# A directive belongs to the interaction of the turn that ACTIVATES a field — the
# prompt-building run that asks the user for it. But pre/post processors run on
# more than that run: the same pre_processor also fires while STORING the user's
# answer, and post_processors / validators / branch conditions fire while
# advancing through the field graph. A directive queued from any of those would
# land on a DIFFERENT interaction than the one it belongs to (the next field's),
# i.e. bleed across turns. So the sink emits ONLY on the activation run; every
# other run gets an inert sink. Processors that must message the user off the
# activation run use the engine return pipeline (``response_directive`` / ``note``),
# which the engine orders, dedups, and binds to the correct turn.
ACTIVATION_PHASE = "prompt"
# Default for any non-activation call_hook dispatch (store / validate / branch /
# post / handlers). Inert: the sink may not queue user content.
ADVANCE_PHASE = "advance"


class InterviewDirectives:
    """Queue user-visible directives onto the live interaction from a processor.

    Instances are cheap and per-call; a processor receives one as its
    ``directives`` kwarg. The interaction is the single source of truth — this is
    a thin, typed facade over ``interaction.add_directive`` so processors never
    reach into ``visitor.interaction`` internals themselves.
    """

    __slots__ = ("_interaction", "phase")

    def __init__(self, interaction: Any, *, phase: str = ACTIVATION_PHASE) -> None:
        self._interaction = interaction
        self.phase = phase

    @property
    def available(self) -> bool:
        """True only on the field-activation run, with a live interaction.

        Inert on every other run (storing an answer, validating, branching,
        post-store, handlers) so a directive never lands on an interaction it
        doesn't belong to. ``add`` / ``tell_user`` short-circuit on False, so a
        processor may call ``tell_user`` unconditionally — the content fires only
        while the field it belongs to is being asked.
        """
        return (
            self.phase == ACTIVATION_PHASE
            and self._interaction is not None
            and hasattr(self._interaction, "add_directive")
        )

    def tell_user(self, content: str) -> bool:
        """Queue user-facing CONTENT, framed as a ``Tell the user:`` directive.

        Use for anything the user must actually read that does not fit the bare
        ``response_directive`` question — option lists, tables, summaries. Returns
        True if queued (False on empty content, no interaction, or a duplicate).
        """
        body = (content or "").strip()
        if not body:
            return False
        framed = (
            body
            if body.lower().startswith("tell the user")
            else f"Tell the user: {body}"
        )
        return self.add(framed)

    def add(self, directive: str, *, source: str = _SOURCE) -> bool:
        """Queue a fully-formed directive verbatim. Escape hatch for callers that
        have already framed the instruction. Returns True if queued."""
        body = (directive or "").strip()
        if not body or not self.available:
            return False
        try:
            return bool(self._interaction.add_directive(body, source))
        except Exception:  # pragma: no cover - defensive; never break a processor
            logger.debug("InterviewDirectives.add failed", exc_info=True)
            return False
