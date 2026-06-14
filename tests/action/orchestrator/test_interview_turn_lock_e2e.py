"""E2E: new-user auto-start opens interview session and materializes interview tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.interview.interview_action import InterviewAction
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


async def test_new_user_auto_start_opens_session_and_seeds_next_field(
    make_orchestrator, make_visitor, tmp_path, monkeypatch
):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: onboarding_interview
interview:
  title: Onboarding
  fields:
    - key: phone
      prompt: What is your best phone number?
      required: true
---
""",
        encoding="utf-8",
    )

    skill = SkillDoc(
        name="onboarding_interview",
        description="Onboarding.",
        body="SOP: collect phone.",
        requires_tools=("interview__next_field", "interview__set_fields"),
        requires_actions=("InterviewAction",),
        task_lock=True,
    )

    interview = InterviewAction()
    interview.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }
    await interview._discover_specs()

    reply_ia = _reply_tool()
    ex = make_orchestrator(
        actions=[interview, reply_ia],
        action_registry={"InterviewAction": interview, "ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "What is your best phone number?"}],
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
        return {"action": "final", "answer": "What is your best phone number?"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = make_visitor(utterance="Hello")
    v.new_user = True
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()
    interview._get_conversation = AsyncMock(return_value=v.conversation)

    await ex.execute(v)

    assert v.conversation.context.get("interview", {}).get("status") == "active"
    assert len(captured) == 1
    assert "interview__next_field" in captured[0]["tools"]
    assert "interview__set_fields" in captured[0]["tools"]
    tool_names = [o.get("tool") for o in captured[0]["observations"]]
    assert "use_skill" in tool_names
    # Prep no longer auto-injects next_field; model calls it per SOP.
    assert "interview__next_field" not in tool_names
