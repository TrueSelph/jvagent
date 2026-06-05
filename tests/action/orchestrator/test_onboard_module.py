"""Unit tests for TaskStore-driven onboard helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from jvagent.action.orchestrator.onboard import (
    has_active_onboard_task,
    is_onboard_skill_done,
    pending_onboard_skills,
    resolve_onboard_locked_skill_doc,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.memory.task_store import Task, TaskStore


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


def test_is_onboard_skill_done_only_on_completed():
    conv = MagicMock()
    conv.tasks = [
        _task("MySkill", "cancelled"),
        _task("MySkill", "failed", "2026-06-04T11:00:00Z"),
    ]
    store = TaskStore(conv)
    assert is_onboard_skill_done(store, "MySkill") is False

    conv.tasks.append(_task("MySkill", "completed", "2026-06-04T13:00:00Z"))
    store = TaskStore(conv)
    assert is_onboard_skill_done(store, "MySkill") is True


def test_has_active_onboard_task():
    conv = MagicMock()
    conv.tasks = [_task("S", "active")]
    store = TaskStore(conv)
    assert has_active_onboard_task(store, "S") is True
    assert has_active_onboard_task(store, "Other") is False


def test_pending_onboard_skills_order():
    conv = MagicMock()
    conv.tasks = [_task("B", "completed")]
    store = TaskStore(conv)
    assert pending_onboard_skills(store, ["A", "B", "C"]) == ["A", "C"]


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
