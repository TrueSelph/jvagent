"""Orchestrator + live TaskStore integration (ADR-0013 lock path)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jvagent.memory.task_store import TaskStore

pytestmark = pytest.mark.asyncio


def _signup(flow_stub_cls, on_exec=None):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            if on_exec:
                on_exec(visitor)
            visitor.interaction.response = "What's your full name?"

    return SignupIA()


def _spy_model(monkeypatch):
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    calls = {"n": 0}

    async def _m(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        finalize=False,
        gear="heavy",
        lean=False,
    ):
        calls["n"] += 1
        return {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)
    return calls


async def test_lock_with_live_taskstore_zero_model_calls(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    """Active flow in TaskStore + lock_active_flow → IA runs, no model calls."""
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda _v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    calls = _spy_model(monkeypatch)

    v = make_visitor(utterance="Who is Eldon Marks?")
    v.interaction.observability_metrics = []
    v.interaction.save = AsyncMock()

    h = await TaskStore(v.conversation).create(
        title="signup",
        description="SignupIA",
        task_type="INTERVIEW",
        owner_action="SignupIA",
    )
    await h.start()

    await ex.execute(v)

    assert ran["n"] == 1
    assert calls["n"] == 0
    assert v.interaction.response == "What's your full name?"


async def test_second_turn_lock_continues_without_model(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    """Multi-turn lock: second utterance still dispatches IA with zero model calls."""
    responses = []

    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            responses.append(visitor.utterance)
            visitor.interaction.response = f"Got it: {visitor.utterance}"

    ia = SignupIA()
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    calls = _spy_model(monkeypatch)

    conversation = None
    for utterance in ("Jane Doe", "jane@example.com"):
        v = make_visitor(utterance=utterance)
        if conversation is None:
            conversation = v.conversation
        else:
            v.conversation = conversation
        v.interaction.observability_metrics = []
        v.interaction.save = AsyncMock()
        if not responses:
            h = await TaskStore(conversation).create(
                title="signup",
                description="SignupIA",
                task_type="INTERVIEW",
                owner_action="SignupIA",
            )
            await h.start()
        await ex.execute(v)

    assert responses == ["Jane Doe", "jane@example.com"]
    assert calls["n"] == 0
