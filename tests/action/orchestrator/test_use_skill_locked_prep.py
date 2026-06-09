"""Mid-loop use_skill must run locked-skill turn prep (message evaluation)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc
from tests.action.interview_action.conftest import ORCHESTRATOR_AGENT_DIR

_OPENING = "Hello my name is Eldon Marks. I'm here to sign up"

pytestmark = pytest.mark.asyncio


def _reply_tool():
    from jvagent.tooling.tool import Tool

    class ReplyIA:
        async def get_tools(self):
            return [
                Tool(
                    name="reply",
                    description="Reply.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None,
                )
            ]

    return ReplyIA()


async def test_use_skill_mid_loop_injects_message_evaluation(
    make_orchestrator, make_visitor, monkeypatch
):
    """Model-driven use_skill on tick 1 must prep evaluation before tick 2."""
    signup = SkillDoc(
        name="signup_interview",
        description="JVAgent training signup.",
        body="SOP: evaluate message, set_field, then reply.",
        requires_tools=(
            "interview__set_field",
            "interview__next_question",
            "interview__get_status",
        ),
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    interview = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    await interview._discover_specs()

    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[
            {
                "action": "tool",
                "tool": "use_skill",
                "args": {"name": "signup_interview"},
            },
            {"action": "tool", "tool": "reply", "args": {}},
        ],
    )
    ex.lock_active_flow = True
    ex.lean_tool_threshold = 0

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda self, _agent: [signup],
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    model_calls: list = []
    call_idx = {"n": 0}
    decisions = [
        {
            "action": "tool",
            "tool": "use_skill",
            "args": {"name": "signup_interview"},
        },
        {"action": "tool", "tool": "reply", "args": {}},
    ]

    async def _spy(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        model_calls.append(
            {
                "observations": list(observations),
                "skills_section": skills_section,
                "tools": [t.name for t in tools],
            }
        )
        idx = call_idx["n"]
        call_idx["n"] += 1
        return (
            decisions[idx]
            if idx < len(decisions)
            else {"action": "final", "answer": ""}
        )

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _spy)

    v = make_visitor(utterance=_OPENING)
    v.new_user = False
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()
    interview._get_conversation = AsyncMock(return_value=v.conversation)

    await ex.execute(v)

    assert len(model_calls) == 2
    second_obs_tools = [o.get("tool") for o in model_calls[1]["observations"]]
    assert "interview__message_evaluation" in second_obs_tools
    assert "Turn-lock is ON" in model_calls[1]["skills_section"]
    eval_obs = next(
        o
        for o in model_calls[1]["observations"]
        if o.get("tool") == "interview__message_evaluation"
    )
    assert "user_name" in eval_obs["observation"]
    assert "Eldon Marks" in eval_obs["observation"]


@pytest.mark.asyncio
async def test_set_field_refresh_replaces_stale_message_evaluation(
    make_orchestrator, make_visitor, monkeypatch
):
    """After a successful store, stale message_evaluation must not linger."""
    signup = SkillDoc(
        name="signup_interview",
        description="JVAgent training signup.",
        body="SOP: evaluate message, set_field, then reply.",
        requires_tools=(
            "interview__set_field",
            "interview__next_question",
            "interview__get_status",
        ),
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    interview = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    await interview._discover_specs()

    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[],
    )
    ex.lock_active_flow = True
    ex.lean_tool_threshold = 0

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda self, _agent: [signup],
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    model_calls: list = []
    decisions = [
        {
            "action": "tool",
            "tool": "use_skill",
            "args": {"name": "signup_interview"},
        },
        {
            "action": "tool",
            "tool": "interview__set_field",
            "args": {"field": "user_name", "value": "Eldon Marks"},
        },
        {
            "action": "tool",
            "tool": "interview__set_field",
            "args": {"field": "user_name", "value": "Eldon Marks"},
        },
        {"action": "tool", "tool": "reply", "args": {}},
    ]

    async def _spy(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        model_calls.append(
            {
                "observations": list(observations),
                "skills_section": skills_section,
            }
        )
        idx = len(model_calls) - 1
        return (
            decisions[idx]
            if idx < len(decisions)
            else {"action": "final", "answer": ""}
        )

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _spy)

    v = make_visitor(utterance=_OPENING)
    v.new_user = False
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()
    interview._get_conversation = AsyncMock(return_value=v.conversation)

    await ex.execute(v)

    assert len(model_calls) == 4
    set_obs = [
        o
        for o in model_calls[2]["observations"]
        if o.get("tool") == "interview__set_field"
    ]
    assert set_obs, "expected first set_field observation before second attempt"
    set_payload = json.loads(set_obs[0]["observation"])
    assert set_payload.get("stored") is True, set_payload
    assert (set_payload.get("response_directive") or "").startswith("Tell the user:")
    third_tools = [o.get("tool") for o in model_calls[2]["observations"]]
    assert "interview__message_evaluation" not in third_tools
    assert "interview__next_question" not in third_tools
    assert "(guard)" in third_tools
    assert "Do NOT call interview__set_field" in model_calls[2]["skills_section"]


@pytest.mark.asyncio
async def test_locked_skill_name_as_tool_gets_steer_not_dispatch(
    make_orchestrator, make_visitor, monkeypatch
):
    """Model naming the locked skill as a tool should steer, not waste a tick."""
    signup = SkillDoc(
        name="signup_interview",
        description="JVAgent training signup.",
        body="SOP.",
        requires_tools=("interview__set_field", "interview__next_question"),
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    interview = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    await interview._discover_specs()
    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[],
    )
    ex.lock_active_flow = True
    ex.lean_tool_threshold = 0

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda self, _agent: [signup],
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    model_calls: list = []
    decisions = [
        {
            "action": "tool",
            "tool": "use_skill",
            "args": {"name": "signup_interview"},
        },
        {"action": "tool", "tool": "signup_interview", "args": {}},
        {"action": "tool", "tool": "reply", "args": {}},
    ]

    async def _spy(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        model_calls.append({"observations": list(observations)})
        idx = len(model_calls) - 1
        return (
            decisions[idx]
            if idx < len(decisions)
            else {"action": "final", "answer": ""}
        )

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _spy)

    v = make_visitor(utterance="Sign me up")
    v.new_user = False
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()
    interview._get_conversation = AsyncMock(return_value=v.conversation)

    await ex.execute(v)

    assert len(model_calls) >= 3
    steer_obs = [
        o for o in model_calls[2]["observations"] if o.get("tool") == "signup_interview"
    ]
    assert steer_obs
    assert "active locked skill" in steer_obs[0]["observation"].lower()
    assert "no such tool" not in steer_obs[0]["observation"].lower()
