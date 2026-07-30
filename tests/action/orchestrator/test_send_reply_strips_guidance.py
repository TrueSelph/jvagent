"""Model-only directive guidance after U+2063 must not leak on literal paths.

When compose=True, guidance/hints stay on the queued directive so ReplyAction
can steer the compose model (it never relays post-marker text to the user).
When compose=False, guidance is stripped before queue/publish.
"""

from unittest.mock import MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.reply.reply_action import DIRECTIVE_GUIDANCE_MARKER

_GUIDANCE = (
    f"{DIRECTIVE_GUIDANCE_MARKER}You may paraphrase slightly but keep the same intent. "
    "Do not ask for other information in this reply. "
    "If the user mentions they have a photo, encourage them to send it."
)

_USER = "Tell the user or ask the user: Please enter the verification code."


@pytest.mark.asyncio
async def test_send_reply_compose_preserves_model_guidance(
    make_orchestrator, monkeypatch
):
    ex = make_orchestrator()

    captured = {}
    interaction = MagicMock()
    interaction.add_directive = lambda text, author: captured.__setitem__(
        "directive", text
    )
    visitor = MagicMock()
    visitor.interaction = interaction

    class _Responder:
        async def respond(self, interaction, visitor=None):
            return ""

    async def _get_responder(self):
        return _Responder()

    monkeypatch.setattr(OrchestratorInteractAction, "get_responder", _get_responder)

    await ex._send_reply(
        visitor,
        f"{_USER}{_GUIDANCE}",
        compose=True,
    )

    assert captured["directive"] == f"{_USER}{_GUIDANCE}"
    assert DIRECTIVE_GUIDANCE_MARKER in captured["directive"]
    assert "paraphrase" in captured["directive"].lower()
    assert "encourage them to send it" in captured["directive"]


@pytest.mark.asyncio
async def test_send_reply_literal_strips_model_guidance(make_orchestrator, monkeypatch):
    ex = make_orchestrator()

    captured = {}
    interaction = MagicMock()
    interaction.add_directive = lambda text, author: captured.__setitem__(
        "directive", text
    )
    interaction.has_emitted = lambda: False
    visitor = MagicMock()
    visitor.interaction = interaction
    published = {}

    async def _publish(self, visitor=None, content=""):
        published["content"] = content

    class _Responder:
        async def gather(self, visitor):
            return False

    async def _get_responder(self):
        return _Responder()

    monkeypatch.setattr(OrchestratorInteractAction, "get_responder", _get_responder)
    monkeypatch.setattr(OrchestratorInteractAction, "publish", _publish)

    await ex._send_reply(
        visitor,
        f"{_USER}{_GUIDANCE}",
        compose=False,
    )

    assert captured["directive"] == _USER
    assert DIRECTIVE_GUIDANCE_MARKER not in captured["directive"]
    assert "paraphrase" not in captured["directive"].lower()
    assert published.get("content") == _USER


@pytest.mark.asyncio
async def test_send_reply_skips_compose_for_bare_relay(make_orchestrator, monkeypatch):
    """Bare directive with no guidance uses gather, not respond (ADR-0041)."""
    ex = make_orchestrator()

    interaction = MagicMock()
    interaction.parameters = []
    interaction.directives = []
    interaction.add_directive = lambda text, author: interaction.directives.append(
        {"action_name": author, "content": text, "executed": False}
    )
    interaction.has_emitted = lambda: False
    interaction.get_unexecuted_directives = lambda: [
        d for d in interaction.directives if not d.get("executed")
    ]
    interaction.get_unexecuted_parameters = lambda: []
    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.channel = "web"

    calls = {"respond": 0, "gather": 0}

    class _Responder:
        async def respond(self, interaction, visitor=None):
            calls["respond"] += 1

        async def gather(self, visitor):
            calls["gather"] += 1
            return True

        def get_contributed_response_parameters(self, interaction):
            return []

    async def _get_responder(self):
        return _Responder()

    monkeypatch.setattr(OrchestratorInteractAction, "get_responder", _get_responder)

    await ex._send_reply(visitor, "Tell the user: hi", compose=True)

    assert calls["respond"] == 0
    assert calls["gather"] == 1
