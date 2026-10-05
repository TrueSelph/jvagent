"""The opt-in pilot and legacy loop are mutually exclusive turn drivers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("legacy", "legacy"),
        ("capability_pilot", "pilot"),
    ],
)
async def test_execute_turn_selects_exactly_one_driver(monkeypatch, mode, expected):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = mode
    calls = []

    async def record(name, *_args, **_kwargs):
        calls.append(name)

    monkeypatch.setattr(OrchestratorInteractAction, "_curate_walk_path", record)
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_run_loop",
        lambda *_args, **_kwargs: record("legacy"),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_run_capability_pilot",
        lambda *_args, **_kwargs: record("pilot"),
    )
    monkeypatch.setattr(OrchestratorInteractAction, "_settle_conversation_cost", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_finalize_proactive_task", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_egress", record)

    await ex._execute_turn(SimpleNamespace(interaction=object()))

    assert calls.count(expected) == 1
    assert calls.count("legacy") + calls.count("pilot") == 1


@pytest.mark.asyncio
async def test_unknown_skill_runtime_fails_closed(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = "future_driver"
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_curate_walk_path",
        AsyncMock(),
    )

    with pytest.raises(ValueError, match="skill_runtime"):
        await ex._execute_turn(SimpleNamespace(interaction=object()))
