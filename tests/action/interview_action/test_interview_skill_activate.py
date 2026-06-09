"""Tests for interview session bootstrap on use_skill activation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    InterviewRegistry,
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.tools import build_tools
from jvagent.action.interview_action.interview_action import (
    InterviewAction,
)

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"


def _interview_action_with_contracts() -> InterviewAction:
    action = InterviewAction()
    action._registry = InterviewRegistry()
    action._registry._specs["onboarding_interview"] = load_interview_spec_from_skill(
        _SKILLS_DIR / "onboarding_interview"
    )
    action._registry._specs["pre_alert_interview"] = load_interview_spec_from_skill(
        _SKILLS_DIR / "pre_alert_interview"
    )
    action._get_conversation = AsyncMock(return_value=None)
    action._ensure_active_task = AsyncMock()
    return action


@pytest.mark.asyncio
async def test_on_skill_activate_returns_observation_for_interview_skill():
    action = _interview_action_with_contracts()
    action._handle_start = AsyncMock(
        return_value=(
            '{"ok": true, "status": "active", "interview_type": "onboarding_interview", '
            '"fields": {}, "missing_required": ["phone_number", "email"]}'
        )
    )

    note = await action.on_skill_activate(
        "onboarding_interview", MagicMock(), user_message="hi"
    )

    assert note is not None
    assert "Interview session ready" in note
    assert "onboarding_interview" in note
    assert "missing_required" in note
    assert "SKILL procedure" in note
    assert "interview__next_question" in note
    action._handle_start.assert_awaited_once_with(
        "onboarding_interview", ANY, user_message="hi"
    )


@pytest.mark.asyncio
async def test_on_skill_activate_omits_next_question_hint_when_complete():
    action = _interview_action_with_contracts()
    action._handle_start = AsyncMock(
        return_value=(
            '{"ok": true, "status": "active", "interview_type": "onboarding_interview", '
            '"fields": {"phone_number": "5912345678"}, "missing_required": []}'
        )
    )

    note = await action.on_skill_activate("onboarding_interview", MagicMock())

    assert note is not None
    assert "New session:" not in note


@pytest.mark.asyncio
async def test_on_skill_activate_returns_guidance_for_non_interview_skill():
    action = _interview_action_with_contracts()
    note = await action.on_skill_activate("faq", MagicMock())
    assert note is not None
    assert (
        "no interview spec" in note.lower()
        or "available interview types" in note.lower()
    )


@pytest.mark.asyncio
async def test_needs_session_rebootstrap_when_no_conversation():
    action = _interview_action_with_contracts()
    assert await action.needs_session_rebootstrap("onboarding_interview", MagicMock())


@pytest.mark.asyncio
async def test_needs_session_rebootstrap_false_when_session_active():
    action = _interview_action_with_contracts()
    conversation = MagicMock()
    conversation.context = {
        "interview": {
            "interview_type": "onboarding_interview",
            "status": "active",
            "fields": {},
            "skipped_fields": [],
        }
    }
    action._get_conversation = AsyncMock(return_value=conversation)

    assert not await action.needs_session_rebootstrap(
        "onboarding_interview", MagicMock()
    )


def test_interview__init_not_registered():
    action = _interview_action_with_contracts()
    names = {t.name for t in build_tools(action)}
    assert "interview__init" not in names
    assert "interview__next_question" in names


def test_core_interview_tools_require_active_session_in_description():
    action = _interview_action_with_contracts()
    by_name = {t.name: t for t in build_tools(action)}
    for tool_name in ("interview__set_fields", "interview__next_question"):
        assert "use_skill" in by_name[tool_name].description.lower()
        assert "active interview session" in by_name[tool_name].description.lower()
