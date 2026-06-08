"""Turn-1 regression: model must not loop on interview__next_question with NO_SESSION."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc

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


async def test_new_user_without_session_prunes_interview_tools_from_surface(
    make_orchestrator, make_visitor, tmp_path, monkeypatch
):
    """When bootstrap fails, interview tools are removed from tools+visible."""
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    # No SKILL.md interview spec — on_skill_activate cannot open a session.

    skill = SkillDoc(
        name="onboarding_interview",
        description="Onboarding.",
        body="SOP: call interview__next_question after use_skill.",
        requires_tools=("interview__next_question", "interview__set_field"),
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    interview = InterviewAction()
    interview.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }

    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Welcome."}],
    )
    ex.auto_start_skills_on_new_user = ["onboarding_interview"]
    ex.lock_active_flow = True
    ex.lean_tool_threshold = 0  # surface all tools — worst case for leakage

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    captured: list = []

    async def _m(
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
        captured.append({"tools": [t.name for t in tools]})
        return {"action": "final", "answer": "Welcome."}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = make_visitor(utterance="Hello")
    v.new_user = True
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()

    await ex.execute(v)

    assert len(captured) == 1
    assert "interview__next_question" not in captured[0]["tools"]
    assert "reply" in captured[0]["tools"]


async def test_contract_reload_after_on_register_empty_registry(
    make_orchestrator, make_visitor, tmp_path, monkeypatch
):
    """Simulate on_register with empty registry; auto-start must still open session."""
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: onboarding_interview
interview:
  title: Onboarding
  questions:
    - name: phone_number
      question: What is your phone number?
      required: true
---
""",
        encoding="utf-8",
    )

    skill = SkillDoc(
        name="onboarding_interview",
        description="Onboarding.",
        body="SOP.",
        requires_tools=("interview__next_question",),
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    interview = InterviewAction()
    interview.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }
    # on_register ran too early — registry empty, metadata was not ready yet.
    assert not interview._registry._specs

    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Phone?"}],
    )
    ex.auto_start_skills_on_new_user = ["onboarding_interview"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    captured: list = []

    async def _m(
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
        captured.append(
            {
                "tools": [t.name for t in tools],
                "observations": list(observations),
            }
        )
        return {"action": "final", "answer": "Phone?"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = make_visitor(utterance="Hello")
    v.new_user = True
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()

    await ex.execute(v)

    assert v.conversation.context.get("interview", {}).get("status") == "active"
    assert "interview__next_question" in captured[0]["tools"]
    tool_names = [o.get("tool") for o in captured[0]["observations"]]
    assert "interview__next_question" in tool_names
