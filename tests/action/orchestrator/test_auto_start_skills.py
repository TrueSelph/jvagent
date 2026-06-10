"""auto_start_skills_on_new_user: use_skill seed on new_user; locked_in when active task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


def _capture_visitor(make_visitor, **kw):
    v = make_visitor(**kw)
    v.interaction.observability_metrics = []
    return v


def _activation(v):
    return next(
        (
            e
            for e in v.interaction.observability_metrics
            if e.get("event_type") == "orchestrator_activation"
        ),
        None,
    )


def _reply_action():
    from jvagent.tooling.tool import Tool

    class ReplyIA:
        async def get_tools(self):
            return [
                Tool(
                    name="reply",
                    description="Reply to the user.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None,
                )
            ]

    return ReplyIA()


def _interview_init_action():
    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_result import ToolResult

    class InterviewToolsAction:
        def get_class_name(self):
            return "InterviewToolsAction"

        async def get_tools(self):
            async def _init(interview_type: str = "", **kwargs):
                return ToolResult(
                    content=(
                        '{"status":"active","next_field":'
                        '[{"question":"What is your best phone number?"}]}'
                    )
                )

            return [
                Tool(
                    name="interview__init",
                    description="Start interview.",
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "interview_type": {"type": "string"},
                        },
                        "required": ["interview_type"],
                    },
                    execute=_init,
                ),
            ]

    return InterviewToolsAction()


def _task_dict(owner: str, status: str) -> dict:
    return {
        "id": f"id_{owner}_{status}",
        "title": owner,
        "description": "",
        "status": status,
        "owner_action": owner,
        "created_at": "2026-06-04T10:00:00Z",
        "updated_at": "2026-06-04T10:00:00Z",
    }


async def test_auto_start_new_user_seeds_use_skill_only(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    skill = SkillDoc(
        name="OnboardingSkill",
        description="Customer onboarding interview.",
        body="SOP: ask for phone number first.",
        requires_tools=("interview__init",),
        locked_in=True,
    )
    interview_ia = _interview_init_action()
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[interview_ia, reply_ia],
        action_registry={
            "InterviewToolsAction": interview_ia,
            "ReplyIA": reply_ia,
        },
        decisions=[{"action": "final", "answer": "What is your best phone number?"}],
    )
    ex.auto_start_skills_on_new_user = ["OnboardingSkill"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        plan_note="",
        **kwargs,
    ):
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "What is your best phone number?"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="hi")
    v.new_user = True
    v.conversation.context = {}
    v.conversation.tasks = []
    v.conversation.save = AsyncMock()

    await ex.execute(v)

    assert len(spied_obs) == 1
    tool_names = [o.get("tool") for o in spied_obs[0]]
    assert "use_skill" in tool_names
    assert "interview__init" not in tool_names
    assert "(auto-start)" in tool_names
    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "OnboardingSkill"


async def test_auto_start_still_runs_when_skill_task_completed(
    make_orchestrator, make_visitor, monkeypatch
):
    """new_user always seeds; completed task does not skip auto-start."""
    from jvagent.action.orchestrator.skills import SkillDoc

    skill = SkillDoc(
        name="OnboardingSkill",
        description="Customer onboarding.",
        body="SOP",
        requires_tools=("interview__init",),
        locked_in=True,
    )
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[reply_ia],
        action_registry={"ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Hello"}],
    )
    ex.auto_start_skills_on_new_user = ["OnboardingSkill"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "Hello"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="hi")
    v.new_user = True
    v.conversation.tasks = [_task_dict("OnboardingSkill", "completed")]
    v.conversation.save = AsyncMock()

    await ex.execute(v)

    tool_names = {o.get("tool") for o in spied_obs[0]}
    assert "use_skill" in tool_names


async def test_auto_start_skipped_when_not_new_user(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    skill = SkillDoc(
        name="OnboardingSkill",
        description="Customer onboarding.",
        body="SOP",
        requires_tools=(),
        locked_in=True,
    )
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[reply_ia],
        action_registry={"ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Hello"}],
    )
    ex.auto_start_skills_on_new_user = ["OnboardingSkill"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "Hello"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="hi")
    v.new_user = False
    v.conversation.tasks = []

    await ex.execute(v)

    tool_names = {o.get("tool") for o in spied_obs[0]}
    assert "use_skill" not in tool_names


async def test_locked_in_returning_user_with_active_task(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    skill = SkillDoc(
        name="OnboardingSkill",
        description="Onboarding.",
        body="SOP",
        requires_tools=("interview__init",),
        locked_in=True,
    )
    interview_ia = _interview_init_action()
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[interview_ia, reply_ia],
        action_registry={
            "InterviewToolsAction": interview_ia,
            "ReplyIA": reply_ia,
        },
        decisions=[{"action": "final", "answer": "Continue"}],
    )
    ex.auto_start_skills_on_new_user = ["OnboardingSkill"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "Continue"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="5926431531")
    v.new_user = False
    v.conversation.tasks = [_task_dict("OnboardingSkill", "active")]

    await ex.execute(v)

    tool_names = {o.get("tool") for o in spied_obs[0]}
    assert "use_skill" not in tool_names
    ev = _activation(v)
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "OnboardingSkill"


async def test_auto_start_two_skills_in_list_order(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    first = SkillDoc(
        name="FirstSkill",
        description="First.",
        body="First SOP",
        requires_tools=(),
        locked_in=False,
    )
    second = SkillDoc(
        name="SecondSkill",
        description="Second.",
        body="Second SOP",
        requires_tools=(),
        locked_in=True,
    )
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[reply_ia],
        action_registry={"ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Hello"}],
    )
    ex.auto_start_skills_on_new_user = ["FirstSkill", "SecondSkill"]
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda self, agent: [first, second],
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "Hello"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="hi")
    v.new_user = True
    v.conversation.tasks = []

    await ex.execute(v)

    use_skill_obs = [o for o in spied_obs[0] if o.get("tool") == "use_skill"]
    assert len(use_skill_obs) == 2
    assert use_skill_obs[0]["args"]["name"] == "FirstSkill"
    assert use_skill_obs[1]["args"]["name"] == "SecondSkill"


async def test_auto_start_accepts_single_string_config(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    skill = SkillDoc(
        name="OnboardingSkill",
        description="Onboarding.",
        body="SOP",
        requires_tools=(),
        locked_in=False,
    )
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[reply_ia],
        action_registry={"ReplyIA": reply_ia},
        decisions=[{"action": "final", "answer": "Hello"}],
    )
    ex.auto_start_skills_on_new_user = "OnboardingSkill"

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    spied_obs: list = []

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
        spied_obs.append(list(observations))
        return {"action": "final", "answer": "Hello"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="hi")
    v.new_user = True
    v.conversation.tasks = []

    await ex.execute(v)

    tool_names = {o.get("tool") for o in spied_obs[0]}
    assert "use_skill" in tool_names
