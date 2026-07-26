"""Every orchestrator reply must carry the parameters and directives in force.

Governance was a convention — route through ReplyAction and the response rules
apply; reach publish() directly and nothing does. The orchestrator's own egress
had a last-resort raw publish, taken whenever the responder was missing or
gather()/respond() raised, which dropped queued directives and applied no
parameters at all.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.parameters import vet_egress

# --- the scrub is now an invariant of publish(), not of one caller -----------


async def test_publish_scrubs_user_facing_content(monkeypatch):
    """A direct publish — an egress fallback, or an action emitting its own
    text — still meets the core response rules."""
    from jvagent.action.interact.base import InteractAction

    class _Concrete(InteractAction):
        async def execute(self, visitor):  # pragma: no cover - unused
            return None

    sent = {}

    class _Bus:
        async def publish(self, **kw):
            sent.update(kw)
            return True

    visitor = MagicMock()
    visitor.response_bus = _Bus()
    visitor.session_id = "s1"
    visitor.stream = False
    visitor.interaction = MagicMock()

    action = _Concrete()
    leak = "I am an AI language model. Your order ships Tuesday."
    await action.publish(visitor, content=leak, stream=False)

    published = str(sent.get("content", ""))
    assert "language model" not in published.lower()
    assert "order ships Tuesday" in published
    assert published == vet_egress(leak)


async def test_publish_leaves_thoughts_alone():
    """Reasoning traces are internal; rewriting them corrupts what the UI shows
    about how the turn ran."""
    from jvagent.action.interact.base import InteractAction

    class _Concrete(InteractAction):
        async def execute(self, visitor):  # pragma: no cover - unused
            return None

    sent = {}

    class _Bus:
        async def publish(self, **kw):
            sent.update(kw)
            return True

    visitor = MagicMock()
    visitor.response_bus = _Bus()
    visitor.session_id = "s1"
    visitor.stream = False
    visitor.interaction = MagicMock()

    trace = "I am an AI language model deciding which tool to call."
    await _Concrete().publish(visitor, content=trace, category="thought", stream=False)
    assert sent.get("content") == trace


# --- the orchestrator owes a compose whenever shaping is pending -------------


def _interaction(directives):
    interaction = MagicMock()
    interaction.parameters = []
    interaction.get_unexecuted_directives = lambda: list(directives)
    return interaction


def test_pending_directives_still_owe_a_compose():
    """The old condition only re-tried when there was no text of our own, so a
    reply WITH text and a queued directive fell through to a raw publish."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    responder = MagicMock()
    responder.apply_channel_format = False
    visitor = MagicMock()
    visitor.channel = "web"

    pending = _interaction([{"content": "Tell the user: something", "executed": False}])
    assert OrchestratorInteractAction._shaping_or_directives_pending(
        responder, pending, visitor
    )

    drained = _interaction([])
    assert not OrchestratorInteractAction._shaping_or_directives_pending(
        responder, drained, visitor
    )


def test_pending_check_never_breaks_egress():
    """A broken interaction must not take the reply path down with it — egress
    runs on the error path too, which is when it matters most."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    broken = MagicMock()
    broken.parameters = []

    def _raise():
        raise RuntimeError("boom")

    broken.get_unexecuted_directives = _raise
    responder = MagicMock()
    responder.apply_channel_format = False
    visitor = MagicMock()
    visitor.channel = "web"
    # The contract is "does not propagate"; the reply must still be deliverable.
    OrchestratorInteractAction._shaping_or_directives_pending(
        responder, broken, visitor
    )
