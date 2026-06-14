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
    resolve_active_task_lock_skill,
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
        extends="action:jvagent/interview",
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

        async def prepare_task_lock_turn(self, skill_name, visitor=None):
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
async def test_resolve_active_task_lock_skill_via_action_resolver():
    skill = SkillDoc(
        name="pre_alert_interview",
        description="d",
        body="b",
        task_lock=True,
        requires_actions=("InterviewAction",),
    )
    onboarding = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        task_lock=True,
        requires_actions=("InterviewAction",),
    )

    class ResolverAction:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        async def resolve_task_lock_skill(self, visitor, skill_docs):
            return onboarding

    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.conversation.context = {}
    doc = await resolve_active_task_lock_skill(
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
async def test_ensure_task_lock_session_rebootstraps_when_missing():
    from jvagent.action.orchestrator.skill_tasks import ensure_task_lock_session

    doc = SkillDoc(
        name="onboarding_interview",
        description="d",
        body="b",
        requires_actions=("InterviewAction",),
        task_lock=True,
    )

    class InterviewActionStub:
        enabled = True

        def get_class_name(self):
            return "InterviewAction"

        needs_task_lock_rebootstrap = AsyncMock(return_value=True)
        on_skill_activate = AsyncMock(return_value="session ready")

    visitor = MagicMock()
    note = await ensure_task_lock_session(
        doc, [InterviewActionStub()], visitor, user_message="5926431531"
    )
    assert note == "session ready"
    InterviewActionStub.on_skill_activate.assert_awaited_once()


# --- Companion capabilities during task-lock -------------------------------


def _skill(name, *, requires_tools=(), task_lock=False, lock_companions=()):
    return SkillDoc(
        name=name,
        description="",
        body="PROC",
        requires_tools=tuple(requires_tools),
        task_lock=task_lock,
        lock_companions=tuple(lock_companions),
    )


def test_resolve_lock_companions_splits_skills_and_globs():
    from jvagent.action.orchestrator.skill_tasks import resolve_lock_companions

    locked = _skill(
        "pre_alert_interview",
        requires_tools=("interview__set_fields",),
        task_lock=True,
        lock_companions=("faq", "find_tool"),
    )
    faq = _skill("faq", requires_tools=("faq__search",))
    skills, globs = resolve_lock_companions(locked, [locked, faq])
    assert [s.name for s in skills] == ["faq"]
    assert globs == ["find_tool"]


def test_resolve_lock_companions_rejects_task_lock_companion():
    from jvagent.action.orchestrator.skill_tasks import resolve_lock_companions

    locked = _skill("a", task_lock=True, lock_companions=("b",))
    other = _skill("b", task_lock=True)  # would seize the lock
    skills, globs = resolve_lock_companions(locked, [locked, other])
    assert skills == []
    assert globs == []  # task_lock companion dropped, not treated as a glob


def test_restrict_surface_includes_companions():
    from jvagent.action.orchestrator.skill_tasks import (
        resolve_lock_companions,
        restrict_tools_to_task_lock_skill,
    )

    locked = _skill(
        "pre_alert_interview",
        requires_tools=("interview__set_fields", "interview__next_field"),
        task_lock=True,
        lock_companions=("faq", "find_tool"),
    )
    faq = _skill("faq", requires_tools=("faq__search",))
    tools = {
        "interview__set_fields": object(),
        "interview__next_field": object(),
        "faq__search": object(),
        "find_tool": object(),
        "use_skill": object(),
        "reply": object(),
        "respond": object(),
        "unrelated_tool": object(),
    }
    visible = set(tools)
    comp_skills, comp_globs = resolve_lock_companions(locked, [locked, faq])
    restricted, restricted_visible, section = restrict_tools_to_task_lock_skill(
        locked,
        tools,
        visible,
        [],
        companion_skills=comp_skills,
        companion_tool_globs=comp_globs,
    )
    # locked tools + companion skill tool + glob tool + use_skill + egress
    assert "interview__set_fields" in restricted
    assert "faq__search" in restricted
    assert "find_tool" in restricted
    assert "use_skill" in restricted
    assert "reply" in restricted
    # not whitelisted -> blocked
    assert "unrelated_tool" not in restricted
    # section advertises companions + return-to-task
    assert "faq" in section
    assert "return to this skill" in section.lower()


def test_restrict_surface_no_companions_unchanged():
    from jvagent.action.orchestrator.skill_tasks import (
        restrict_tools_to_task_lock_skill,
    )

    locked = _skill("x", requires_tools=("x__do",), task_lock=True)
    tools = {
        "x__do": object(),
        "reply": object(),
        "respond": object(),
        "other": object(),
    }
    restricted, _, section = restrict_tools_to_task_lock_skill(
        locked,
        tools,
        set(tools),
        [],
    )
    assert set(restricted) == {"x__do", "reply", "respond"}
    assert "companion" not in section.lower()
