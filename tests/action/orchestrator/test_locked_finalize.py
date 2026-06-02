"""Locked turn path must still run plan finalization."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


async def test_locked_path_calls_finalize_plan(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up"]
        description = "Signup."

        async def execute(self, visitor):
            visitor.interaction.response = "done"

    ia = SignupIA()
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    finalize = AsyncMock()
    monkeypatch.setattr(OrchestratorInteractAction, "_finalize_plan", finalize)
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")

    await ex.execute(make_visitor(utterance="yes"))

    finalize.assert_awaited_once()
