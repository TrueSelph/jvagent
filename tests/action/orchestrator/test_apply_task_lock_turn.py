"""Generic apply_task_lock_turn — bound-action hook protocol."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jvagent.action.orchestrator.skill_tasks import apply_task_lock_turn
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.action.skill_spec.task_lock import TaskLockPrep


async def test_apply_task_lock_turn_uses_bound_action_hooks():
    skill = SkillDoc(
        name="MySkill",
        description="d",
        body="SOP body",
        requires_tools=("my__tool",),
        requires_actions=("BoundAction",),
        task_lock=True,
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_task_lock_rebootstrap(self, skill_name, visitor=None):
            return False

        async def task_lock_runtime_ready(self, skill_name, visitor=None):
            return True

        async def prepare_task_lock_turn(self, skill_name, visitor=None):
            return TaskLockPrep(
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

    out_tools, out_visible, section = await apply_task_lock_turn(
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


async def test_apply_task_lock_turn_reply_only_when_not_ready():
    skill = SkillDoc(
        name="MySkill",
        description="d",
        body="SOP",
        requires_tools=("my__tool",),
        requires_actions=("BoundAction",),
        task_lock=True,
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_task_lock_rebootstrap(self, skill_name, visitor=None):
            return True

        async def on_skill_activate(self, skill_name, visitor=None, *, user_message=""):
            return "activation failed"

        async def task_lock_runtime_ready(self, skill_name, visitor=None):
            return False

    tools = {"my__tool": MagicMock(), "reply": MagicMock()}
    visible = set(tools)
    observations: list = []

    out_tools, out_visible, section = await apply_task_lock_turn(
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


async def test_apply_task_lock_turn_binds_interview_over_api_dependency():
    """Zoon regression: dual requires-actions + API listed first still prep via Interview."""
    skill = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="SOP body",
        requires_tools=("onboarding_interview__send_otp",),
        requires_actions=("ZoonAPIAction", "InterviewAction"),
        extends="action:jvagent/interview",
        task_lock=True,
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

        async def needs_task_lock_rebootstrap(self, skill_name, visitor=None):
            return False

        async def task_lock_runtime_ready(self, skill_name, visitor=None):
            return True

        prepare_task_lock_turn = AsyncMock(
            return_value=TaskLockPrep(runtime_ready=True)
        )

    tools = {
        "onboarding_interview__send_otp": MagicMock(),
        "reply": MagicMock(),
    }
    visible = set(tools)
    observations: list = []
    interview = InterviewAction()

    await apply_task_lock_turn(
        skill,
        [ZoonAPIAction(), interview],
        MagicMock(),
        user_message="5926431531",
        tools=tools,
        visible=visible,
        activated=[],
        observations=observations,
    )

    interview.prepare_task_lock_turn.assert_awaited_once()
    assert not observations


async def test_apply_task_lock_turn_skips_prep_when_activation_catalog_present():
    """Same-turn use_skill catalog must not be duplicated by prepare_task_lock_turn."""
    skill = SkillDoc(
        name="signup_interview",
        description="d",
        body="SOP body",
        requires_tools=("interview__set_fields",),
        requires_actions=("BoundAction",),
        task_lock=True,
    )

    prep_mock = AsyncMock(
        return_value=TaskLockPrep(
            runtime_ready=True,
            observations=[
                {
                    "tool": "interview__get_status",
                    "args": {},
                    "observation": (
                        '{"ok": true, "interview_type": "signup_interview", '
                        '"field_reference": []}'
                    ),
                    "kind": "server_prep",
                }
            ],
        )
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_task_lock_rebootstrap(self, skill_name, visitor=None):
            return False

        async def task_lock_runtime_ready(self, skill_name, visitor=None):
            return True

        prepare_task_lock_turn = prep_mock

    tools = {"interview__set_fields": MagicMock(), "reply": MagicMock()}
    visible = set(tools)
    observations = [
        {
            "tool": "use_skill",
            "args": {"name": "signup_interview"},
            "observation": (
                "Activated skill 'signup_interview'. Tools now callable: "
                "interview__set_fields.\n\n"
                '{"ok": true, "status": "active", '
                '"interview_type": "signup_interview", '
                '"field_reference": [{"key": "user_name"}]}'
            ),
        }
    ]

    await apply_task_lock_turn(
        skill,
        [BoundAction()],
        MagicMock(),
        user_message="hi",
        tools=tools,
        visible=visible,
        activated=["signup_interview"],
        observations=observations,
    )

    prep_mock.assert_awaited_once()
    assert all(o.get("tool") != "interview__get_status" for o in observations)
    assert len(observations) == 1


async def test_apply_task_lock_turn_injects_prep_without_activation():
    """Resumed locked turns still get prepare_task_lock_turn status."""
    skill = SkillDoc(
        name="signup_interview",
        description="d",
        body="SOP body",
        requires_tools=("interview__set_fields",),
        requires_actions=("BoundAction",),
        task_lock=True,
    )

    class BoundAction:
        enabled = True

        def get_class_name(self):
            return "BoundAction"

        async def needs_task_lock_rebootstrap(self, skill_name, visitor=None):
            return False

        async def task_lock_runtime_ready(self, skill_name, visitor=None):
            return True

        async def prepare_task_lock_turn(self, skill_name, visitor=None):
            return TaskLockPrep(
                runtime_ready=True,
                observations=[
                    {
                        "tool": "interview__get_status",
                        "args": {},
                        "observation": (
                            '{"ok": true, "interview_type": "signup_interview", '
                            '"field_reference": [{"key": "user_name"}]}'
                        ),
                    }
                ],
            )

    tools = {"interview__set_fields": MagicMock(), "reply": MagicMock()}
    observations: list = []

    await apply_task_lock_turn(
        skill,
        [BoundAction()],
        MagicMock(),
        user_message="hi",
        tools=tools,
        visible=set(tools),
        activated=["signup_interview"],
        observations=observations,
    )

    assert any(o.get("tool") == "interview__get_status" for o in observations)


def test_activated_skill_section_text_surfaces_procedure():
    from jvagent.action.orchestrator.skill_tasks import activated_skill_section_text

    doc = SkillDoc(name="web_lookup", description="d", body="1. search\n2. summarize")
    section = activated_skill_section_text(doc)
    assert "ACTIVE SKILL: web_lookup" in section
    assert "PROCEDURE:\n1. search" in section
