"""Tests for interview SKILL-task lifecycle tracking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview import tasks


def _task(
    owner_action: str,
    *,
    task_type: str = "SKILL",
    interview_type: str | None = None,
    interview_managed: bool = False,
    updated_at: str = "",
):
    handle = MagicMock()
    handle.owner_action = owner_action
    handle.task_type = task_type
    handle.data = {}
    if interview_type:
        handle.data["interview_type"] = interview_type
    if interview_managed:
        handle.data["interview_managed"] = True
    handle.updated_at = updated_at
    handle.id = f"{owner_action}-{interview_type or 'task'}"
    handle.cancel = AsyncMock()
    handle.complete = AsyncMock()
    handle.update = AsyncMock()
    return handle


def test_find_existing_active_task_matches_owner_action():
    onboarding = _task("onboarding_interview")
    pre_alert = _task("pre_alert_interview")

    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", "onboarding_interview"): [onboarding],
            ("active", "pre_alert_interview"): [pre_alert],
        }.get((status, owner_action), [])
    )

    visitor = MagicMock()
    visitor.tasks = store

    found = tasks._find_existing_active_task(visitor, "pre_alert_interview")
    assert found is pre_alert

    found_onboard = tasks._find_existing_active_task(visitor, "onboarding_interview")
    assert found_onboard is onboarding


def test_find_existing_active_task_no_match_returns_none():
    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", "pre_alert_interview"): [],
        }.get((status, owner_action), [])
    )

    visitor = MagicMock()
    visitor.tasks = store

    found = tasks._find_existing_active_task(visitor, "pre_alert_interview")
    assert found is None


@pytest.mark.asyncio
async def test_close_task_filters_by_spec_name():
    onboarding = _task("onboarding_interview", interview_managed=True)
    pre_alert = _task("pre_alert_interview", interview_managed=True)
    unrelated = _task("data_export_skill", interview_managed=False)

    store = MagicMock()
    store.list = MagicMock(
        side_effect=lambda status="active", owner_action=None: {
            ("active", None): [onboarding, pre_alert, unrelated],
        }.get((status, owner_action), [])
    )

    visitor = MagicMock()
    visitor.tasks = store

    await tasks.close_task(
        visitor, status="cancelled", spec_name="onboarding_interview"
    )

    onboarding.cancel.assert_awaited_once()
    pre_alert.cancel.assert_not_awaited()
    unrelated.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_task_exclude_only_touches_interview_managed_tasks():
    onboarding = _task("onboarding_interview", interview_managed=True)
    pre_alert = _task("pre_alert_interview", interview_managed=True)
    unrelated = _task("data_export_skill")

    store = MagicMock()
    store.list = MagicMock(return_value=[onboarding, pre_alert, unrelated])

    visitor = MagicMock()
    visitor.tasks = store

    await tasks.close_task(
        visitor,
        status="cancelled",
        exclude_spec_name="onboarding_interview",
    )

    onboarding.cancel.assert_not_awaited()
    pre_alert.cancel.assert_awaited_once()
    unrelated.cancel.assert_not_awaited()
