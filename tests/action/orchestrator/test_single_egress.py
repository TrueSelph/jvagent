"""_egress — the single post-loop egress authority (gather → clarify).

The orchestrator (author) queues directives; the responder gathers them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_egress_noop_when_already_emitted(monkeypatch):
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.mark_emitted()
    interaction.add_directive("Tell the user: hi", "IA")
    visitor = SimpleNamespace(interaction=interaction)
    responder = SimpleNamespace(gather=AsyncMock())
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=responder)
    )

    await ex._egress(visitor)

    responder.gather.assert_not_awaited()


@pytest.mark.asyncio
async def test_egress_gathers_directives_when_not_emitted(monkeypatch):
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.add_directive("Tell the user: hi", "IA")

    async def _gather(visitor=None):
        interaction.mark_emitted()
        return True

    responder = SimpleNamespace(gather=AsyncMock(side_effect=_gather))
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=responder)
    )
    visitor = SimpleNamespace(interaction=interaction)

    await ex._egress(visitor)

    responder.gather.assert_awaited()


@pytest.mark.asyncio
async def test_egress_falls_back_to_clarify_when_nothing(monkeypatch):
    ex = OrchestratorInteractAction()
    interaction = Interaction()  # no directives, not emitted
    visitor = SimpleNamespace(interaction=interaction)
    seen: list = []

    async def _gather(visitor=None):
        pending = interaction.get_unexecuted_directives()
        if pending:
            seen.append(pending[-1]["content"])
            interaction.mark_emitted()
            return True
        return False

    responder = SimpleNamespace(gather=AsyncMock(side_effect=_gather))
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=responder)
    )

    await ex._egress(visitor)

    # First gather finds nothing; the clarify fallback queues + gathers it.
    assert any(ex.clarify_text in s for s in seen)


@pytest.mark.asyncio
async def test_send_reply_queues_reply_as_orchestrator_directive(monkeypatch):
    """The orchestrator (author) queues the model's reply onto
    interaction.directives, attributed to itself; ReplyAction gathers it. So
    interaction.directives is populated even for model-authored / skill turns."""
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    visitor = SimpleNamespace(interaction=interaction)
    gathered: list = []

    async def _gather(visitor=None):
        gathered.extend(interaction.get_unexecuted_directives())
        interaction.mark_emitted()
        return True

    responder = SimpleNamespace(gather=AsyncMock(side_effect=_gather))
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=responder)
    )

    await ex._send_reply(visitor, "What is your full name?")

    assert [d["action_name"] for d in interaction.directives] == [
        "OrchestratorInteractAction"
    ]
    assert (
        interaction.directives[0]["content"] == "Tell the user or ask the user: What is your full name?"
    )
    assert gathered  # the responder gathered the queued directive
