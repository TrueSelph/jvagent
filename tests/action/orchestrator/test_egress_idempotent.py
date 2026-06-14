"""Once a turn has emitted, no second user message is sent."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_finalize_directives_skipped_when_emitted(monkeypatch):
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.mark_emitted()
    interaction.add_directive("Tell the user: hi", "SomeIA")
    visitor = SimpleNamespace(interaction=interaction)
    responder = SimpleNamespace(respond=AsyncMock())
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "get_responder",
        AsyncMock(return_value=responder),
    )

    await ex._finalize_directives(visitor)

    responder.respond.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_directives_runs_when_not_emitted(monkeypatch):
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.add_directive("Tell the user: hi", "SomeIA")
    visitor = SimpleNamespace(interaction=interaction)
    responder = SimpleNamespace(respond=AsyncMock())
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "get_responder",
        AsyncMock(return_value=responder),
    )

    await ex._finalize_directives(visitor)

    responder.respond.assert_awaited_once()
