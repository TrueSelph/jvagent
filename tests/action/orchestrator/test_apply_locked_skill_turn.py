"""Generic apply_locked_skill_turn — bound-action hook protocol."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.skill_tasks import (
    LockedSkillPrep,
    apply_locked_skill_turn,
)
from jvagent.action.orchestrator.skills import SkillDoc

pytestmark = pytest.mark.asyncio


async def test_apply_locked_skill_turn_uses_bound_action_hooks():
    skill = SkillDoc(
        name="MySkill",
        description="d",
        body="SOP body",
        requires_tools=("my__tool",),
        requires_actions=("BoundAction",),
        locked_in=True,
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_session_rebootstrap(self, skill_name, visitor=None):
            return False

        async def skill_runtime_ready(self, skill_name, visitor=None):
            return True

        async def prepare_locked_skill_turn(self, skill_name, visitor=None):
            return LockedSkillPrep(
                runtime_ready=True,
                observations=[
                    {"tool": "my__tool", "args": {}, "observation": "seeded"}
                ],
            )

    tools = {
        "my__tool": MagicMock(),
        "reply": MagicMock(),
        "other": MagicMock(),
    }
    visible = {"my__tool", "reply", "other"}
    activated: list = []
    observations: list = []
    visitor = MagicMock()

    out_tools, out_visible, section = await apply_locked_skill_turn(
        skill,
        [BoundAction()],
        visitor,
        user_message="hi",
        tools=tools,
        visible=visible,
        activated=activated,
        observations=observations,
    )

    assert "my__tool" in out_tools
    assert "other" not in out_tools
    assert any(o.get("observation") == "seeded" for o in observations)
    assert "ACTIVE SKILL IN PROGRESS" in section


async def test_apply_locked_skill_turn_reply_only_when_not_ready():
    skill = SkillDoc(
        name="MySkill",
        description="d",
        body="SOP",
        requires_tools=("my__tool",),
        requires_actions=("BoundAction",),
        locked_in=True,
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_session_rebootstrap(self, skill_name, visitor=None):
            return True

        async def on_skill_activate(self, skill_name, visitor=None, *, user_message=""):
            return "activation failed"

        async def skill_runtime_ready(self, skill_name, visitor=None):
            return False

    tools = {"my__tool": MagicMock(), "reply": MagicMock()}
    visible = set(tools)
    observations: list = []

    out_tools, out_visible, section = await apply_locked_skill_turn(
        skill,
        [BoundAction()],
        MagicMock(),
        user_message="hi",
        tools=tools,
        visible=visible,
        activated=[],
        observations=observations,
    )

    assert set(out_tools) == {"reply"}
    assert "my__tool" not in out_visible
    assert "reply to the user only" in section.lower() or "not ready" in section.lower()


async def test_apply_locked_skill_turn_binds_interview_over_api_dependency():
    """Zoon regression: dual requires-actions + API listed first still prep via Interview."""
    skill = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="SOP body",
        requires_tools=("onboarding_interview__send_otp",),
        requires_actions=("ZoonAPIAction", "InterviewAction"),
        extends="action:jvagent/interview",
        locked_in=True,
    )

    class ZoonAPIAction:
        enabled = True

        def get_class_name(self):
            return "ZoonAPIAction"

        def get_action_ref(self):
            return "zoon/zoon_api_action"

    class InterviewAction:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        def get_action_ref(self):
            return "jvagent/interview"

        async def needs_session_rebootstrap(self, skill_name, visitor=None):
            return False

        async def skill_runtime_ready(self, skill_name, visitor=None):
            return True

        prepare_locked_skill_turn = AsyncMock(
            return_value=LockedSkillPrep(runtime_ready=True)
        )

    tools = {
        "onboarding_interview__send_otp": MagicMock(),
        "reply": MagicMock(),
    }
    visible = set(tools)
    observations: list = []
    interview = InterviewAction()

    await apply_locked_skill_turn(
        skill,
        [ZoonAPIAction(), interview],
        MagicMock(),
        user_message="5926431531",
        tools=tools,
        visible=visible,
        activated=[],
        observations=observations,
    )

    interview.prepare_locked_skill_turn.assert_awaited_once()
    assert not observations
