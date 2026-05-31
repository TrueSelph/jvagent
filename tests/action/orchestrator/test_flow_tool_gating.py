"""IA-tool visibility gating (ADR-0012): a flow's tool is in the prompt only
when it's the active flow or the utterance is anchor-relevant — never on idle
turns (fixing the 'interview always triggered' misroute)."""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


def _signup(flow_stub_cls):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up for jvagent training", "register for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            pass

    return SignupIA()


async def test_anchor_relevant_matching():
    rel = OrchestratorInteractAction._anchor_relevant
    anchors = ["sign up for jvagent training", "register for training"]
    assert rel("I'd like to sign up for jvagent training", anchors) is True
    assert rel("can you register me for training?", anchors) is True
    assert rel("Hello there", anchors) is False
    assert rel("What time is it?", anchors) is False
    assert rel("Who is Eldon Marks?", anchors) is False


async def test_ia_tool_hidden_on_idle_turn(
    make_orchestrator, make_visitor, flow_stub_cls
):
    ia = _signup(flow_stub_cls)
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    v = make_visitor(utterance="Hello there")
    visible: set = set()
    tools = await ex._assemble_tools(
        v, [], visible, flow_owner=None, utterance="Hello there"
    )
    assert "SignupIA" in tools  # built into the full surface (findable)
    assert "SignupIA" not in visible  # but NOT shown to the model


async def test_ia_tool_visible_on_anchor_relevant_turn(
    make_orchestrator, make_visitor, flow_stub_cls
):
    ia = _signup(flow_stub_cls)
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    v = make_visitor(utterance="I'd like to sign up for jvagent training")
    visible: set = set()
    await ex._assemble_tools(
        v,
        [],
        visible,
        flow_owner=None,
        utterance="I'd like to sign up for jvagent training",
    )
    assert "SignupIA" in visible  # first-entry: surfaced by anchor relevance


async def test_ia_tool_visible_when_active_flow(
    make_orchestrator, make_visitor, flow_stub_cls
):
    ia = _signup(flow_stub_cls)
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    v = make_visitor(utterance="Who is Eldon Marks?")
    visible: set = set()
    await ex._assemble_tools(
        v,
        [],
        visible,
        flow_owner="SignupIA",  # active flow
        utterance="Who is Eldon Marks?",
    )
    assert "SignupIA" in visible  # active flow stays surfaced for continuation
