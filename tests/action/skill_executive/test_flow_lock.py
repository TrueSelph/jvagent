"""lock_active_flow (ADR-0013): when on, an active flow control-task restricts
the loop's callable surface to the owning IA's tool, which is dispatched
immediately (mechanistic turn-lock — no model round-trip, even for an off-topic
utterance). When off, continuation is model-mediated through the loop.
``active_flow_owner`` is stubbed and ``_run_model`` is spied so each test asserts
the routing decision without a live TaskStore or model."""

from __future__ import annotations

import pytest

import jvagent.action.skill_executive.skill_executive_interact_action as sei
from jvagent.action.skill_executive.skill_executive_interact_action import (
    SkillExecutiveInteractAction,
)

pytestmark = pytest.mark.asyncio


def _signup(flow_stub_cls, on_exec=None):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            if on_exec:
                on_exec(visitor)

    return SignupIA()


def _spy_model(monkeypatch):
    """Count model round-trips; each returns a no-op 'final' decision."""
    calls = {"n": 0}

    async def _m(self, visitor, utterance, history, tools, observations, flow_note=""):
        calls["n"] += 1
        return {"action": "final", "answer": ""}

    monkeypatch.setattr(SkillExecutiveInteractAction, "_run_model", _m)
    return calls


async def test_lock_on_restricts_surface_to_owning_ia(
    make_skill_executive, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_skill_executive(actions=[ia], action_registry={"SignupIA": ia})
    assert ex.lock_active_flow is True  # default

    monkeypatch.setattr(sei, "active_flow_owner", lambda v: "SignupIA")
    calls = _spy_model(monkeypatch)

    # Off-topic utterance mid-flow: the surface is restricted to the IA's tool,
    # which is dispatched directly — no model round-trip.
    await ex.execute(make_visitor(utterance="Who is Eldon Marks?"))

    assert ran["n"] == 1  # owning IA's tool dispatched (forwarded to execute)
    assert calls["n"] == 0  # restricted surface → loop never calls the model


async def test_lock_off_is_model_mediated(
    make_skill_executive, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_skill_executive(actions=[ia], action_registry={"SignupIA": ia})
    ex.lock_active_flow = False

    monkeypatch.setattr(sei, "active_flow_owner", lambda v: "SignupIA")
    calls = _spy_model(monkeypatch)

    await ex.execute(make_visitor(utterance="Who is Eldon Marks?"))

    assert ran["n"] == 0  # IA not auto-dispatched
    assert calls["n"] >= 1  # continuation is model-mediated via the loop


async def test_lock_on_no_active_task_runs_loop(
    make_skill_executive, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_skill_executive(actions=[ia], action_registry={"SignupIA": ia})

    monkeypatch.setattr(sei, "active_flow_owner", lambda v: None)
    calls = _spy_model(monkeypatch)

    await ex.execute(make_visitor(utterance="Hello there"))

    assert ran["n"] == 0  # nothing to lock onto
    assert calls["n"] >= 1  # normal loop runs the model
