"""Orchestrator-authored replies must never leak model-only directive guidance.

A directive carries model-facing guidance after the U+2063 marker ("You may
paraphrase…", tool-chain hints). The orchestrator composes/relays its terminal
reply directly (ADR-0025), so that guidance is vestigial and must be stripped —
otherwise a weak compose model echoes it to the user verbatim.
"""

from unittest.mock import MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

_GUIDANCE = (
    "⁣You may paraphrase slightly but keep the same intent. "
    "Do not ask for other information in this reply."
)


@pytest.mark.asyncio
async def test_send_reply_strips_model_guidance(make_orchestrator, monkeypatch):
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
        f"Tell the user: Please enter the verification code.{_GUIDANCE}",
        compose=True,
    )

    assert captured["directive"] == "Tell the user: Please enter the verification code."
    assert "⁣" not in captured["directive"]
    assert "paraphrase" not in captured["directive"].lower()
