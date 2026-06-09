"""Unit tests for TaskStore-driven skill_tasks helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.skill_tasks import (
    action_for_skill,
    compose_skill_activate_hooks,
    has_active_skill_task,
    is_skill_task_done,
    pending_auto_start_skills,
    resolve_active_locked_skill,
    resolve_onboard_locked_skill_doc,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.memory.task_store import TaskStore


def _task(owner: str, status: str, updated_at: str = "2026-06-04T12:00:00Z") -> dict:
    return {
        "id": f"task_{owner}_{status}",
        "title": owner,
        "description": "",
        "status": status,
        "owner_action": owner,
        "updated_at": updated_at,
        "created_at": updated_at,
    }


def test_is_skill_task_done_only_on_completed():
    conv = MagicMock()
    conv.tasks = [
        _task("MySkill", "cancelled"),
        _task("MySkill", "failed", "2026-06-04T11:00:00Z"),
    ]
    store = TaskStore(conv)
    assert is_skill_task_done(store, "MySkill") is False

    conv.tasks.append(_task("MySkill", "completed", "2026-06-04T13:00:00Z"))
    store = TaskStore(conv)
    assert is_skill_task_done(store, "MySkill") is True


def test_has_active_skill_task():
    conv = MagicMock()
    conv.tasks = [_task("S", "active")]
    store = TaskStore(conv)
    assert has_active_skill_task(store, "S") is True
    assert has_active_skill_task(store, "Other") is False


def test_pending_auto_start_skills_order():
    conv = MagicMock()
    conv.tasks = [_task("B", "completed")]
    store = TaskStore(conv)
    assert pending_auto_start_skills(store, ["A", "B", "C"]) == ["A", "C"]


def test_resolve_onboard_locked_skill_doc():
    skill = SkillDoc(
        name="OnboardSkill",
        description="d",
        body="b",
        locked_in=True,
    )
    conv = MagicMock()
    conv.tasks = [_task("OnboardSkill", "active")]
    visitor = MagicMock()
    visitor.conversation = conv
    doc = resolve_onboard_locked_skill_doc(
        visitor,
        [skill],
        ["OnboardSkill"],
        lock_active_flow=True,
    )
    assert doc is not None
    assert doc.name == "OnboardSkill"

    conv.tasks = [_task("OnboardSkill", "completed")]
    doc = resolve_onboard_locked_skill_doc(
        visitor,
        [skill],
        ["OnboardSkill"],
        lock_active_flow=True,
    )
    assert doc is None


def test_action_for_skill_requires_actions():
    doc = SkillDoc(
        name="S",
        description="d",
        body="b",
        requires_actions=("InterviewAction",),
    )

    class InterviewAction:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

    class Other:
        enabled = True

        def get_class_name(self):
            return "Other"

    bound = action_for_skill(doc, [Other(), InterviewAction()])
    assert bound is not None
    assert bound.get_class_name() == "InterviewAction"


def test_action_for_skill_binds_extends_target_over_agent_order():
    doc = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        requires_actions=("ZoonAPIAction", "InterviewAction"),
        extends="action:jvagent/interview_action",
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
            return "jvagent/interview_action"

    bound = action_for_skill(doc, [ZoonAPIAction(), InterviewAction()])
    assert bound.get_class_name() == "InterviewAction"


def test_action_for_skill_prefers_lifecycle_protocol():
    doc = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        requires_actions=("ZoonAPIAction", "InterviewAction"),
    )

    class ZoonAPIAction:
        enabled = True

        def get_class_name(self):
            return "ZoonAPIAction"

    class InterviewAction:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        async def prepare_locked_skill_turn(self, skill_name, visitor=None):
            return None

    bound = action_for_skill(doc, [ZoonAPIAction(), InterviewAction()])
    assert bound.get_class_name() == "InterviewAction"


def test_action_for_skill_uses_requires_order_not_agent_order():
    doc = SkillDoc(
        name="S",
        description="d",
        body="b",
        requires_actions=("ActionA", "ActionB"),
    )

    class ActionA:
        enabled = True

        def get_class_name(self):
            return "ActionA"

    class ActionB:
        enabled = True

        def get_class_name(self):
            return "ActionB"

    bound = action_for_skill(doc, [ActionB(), ActionA()])
    assert bound.get_class_name() == "ActionA"


@pytest.mark.asyncio
async def test_resolve_active_locked_skill_via_action_resolver():
    skill = SkillDoc(
        name="pre_alert_interview",
        description="d",
        body="b",
        locked_in=True,
        requires_actions=("InterviewAction",),
    )
    onboarding = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        locked_in=True,
        requires_actions=("InterviewAction",),
    )

    class ResolverAction:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        async def resolve_locked_skill(self, visitor, skill_docs):
            return onboarding

    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.conversation.context = {}
    doc = await resolve_active_locked_skill(
        visitor,
        [onboarding, skill],
        [ResolverAction()],
        lock_active_flow=True,
    )
    assert doc is not None
    assert doc.name == "onboarding_interview"


@pytest.mark.asyncio
async def test_compose_skill_activate_hooks_calls_bound_action():
    doc = SkillDoc(
        name="S",
        description="d",
        body="b",
        requires_actions=("BootstrapAction",),
    )

    class BootstrapAction:
        enabled = True

        def get_class_name(self):
            return "BootstrapAction"

        on_skill_activate = AsyncMock(return_value="bootstrapped")

    activate, _ = compose_skill_activate_hooks([BootstrapAction()], MagicMock(), None)
    note = await activate(doc)
    assert note == "bootstrapped"


@pytest.mark.asyncio
async def test_ensure_locked_skill_session_rebootstraps_when_missing():
    from jvagent.action.orchestrator.skill_tasks import ensure_locked_skill_session

    doc = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        requires_actions=("InterviewAction",),
        locked_in=True,
    )

    class InterviewActionStub:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        needs_session_rebootstrap = AsyncMock(return_value=True)
        on_skill_activate = AsyncMock(return_value="session ready")

    visitor = MagicMock()
    note = await ensure_locked_skill_session(
        doc, [InterviewActionStub()], visitor, user_message="5926431531"
    )
    assert note == "session ready"
    InterviewActionStub.on_skill_activate.assert_awaited_once()
