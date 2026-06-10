"""Tests for interview-type-aware task tracking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action import tasks


def _task(owner_action: str, interview_type: str | None = None, updated_at: str = ""):
    handle = MagicMock()
    handle.owner_action = owner_action
    handle.data = {"interview_type": interview_type} if interview_type else {}
    handle.updated_at = updated_at
    handle.id = f"{owner_action}-{interview_type or 'skill'}"
    handle.cancel = AsyncMock()
    handle.complete = AsyncMock()
    return handle


def test_find_existing_active_task_matches_interview_type():
    onboarding = _task("InterviewAction", "onboarding_interview")
    pre_alert = _task("InterviewAction", "pre_alert_interview")

    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", "onboarding_interview"): [],
            ("active", "InterviewAction"): [onboarding, pre_alert],
        }.get((status, owner_action), [])
    )

    visitor = MagicMock()
    visitor.tasks = store

    found = tasks._find_existing_active_task(visitor, "pre_alert_interview")
    assert found is pre_alert

    found_onboard = tasks._find_existing_active_task(visitor, "onboarding_interview")
    assert found_onboard is onboarding


def test_find_existing_active_task_finds_skill_task():
    skill_task = _task("pre_alert_interview")
    ia_task = _task("InterviewAction", "onboarding_interview")

    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", "pre_alert_interview"): [skill_task],
            ("active", "InterviewAction"): [ia_task],
        }.get((status, owner_action), [])
    )

    visitor = MagicMock()
    visitor.tasks = store

    found = tasks._find_existing_active_task(visitor, "pre_alert_interview")
    assert found is skill_task


@pytest.mark.asyncio
async def test_close_task_filters_by_spec_name():
    onboarding = _task("InterviewAction", "onboarding_interview")
    pre_alert = _task("InterviewAction", "pre_alert_interview")
    skill = _task("onboarding_interview")

    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", "InterviewAction"): [onboarding, pre_alert],
            ("active", "onboarding_interview"): [skill],
        }.get((status, owner_action), [])
    )
    store.delete = AsyncMock()

    visitor = MagicMock()
    visitor.tasks = store

    await tasks.close_task(
        visitor, status="cancelled", spec_name="onboarding_interview"
    )

    onboarding.cancel.assert_awaited_once()
    pre_alert.cancel.assert_not_awaited()
    skill.cancel.assert_awaited_once()
    store.delete.assert_awaited_once_with(onboarding.id)
