"""Tests for interview__next_question."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.interview_action import (
    InterviewAction,
)

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"
_ONBOARDING_SKILL = _SKILLS_DIR / "onboarding_interview"


@pytest.fixture
def onboarding_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.mark.asyncio
async def test_next_question_redirects_to_review_when_done(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.fields = {
        "phone_number": "5912345678",
        "email": "user@example.com",
        "id_number": "12345678",
        "full_name": "Jane Doe",
        "date_of_birth": "01-01-1990",
    }
    session.skipped_fields.add("id_card")
    session.skipped_fields.add("otp_code")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))

    result = json.loads(await action._handle_next_question())

    assert result["ok"] is True
    assert result["next_tool"] == "interview__review"
    assert "interview__review" in result["response_directive"]


@pytest.mark.asyncio
async def test_next_question_no_session_returns_ok_false(onboarding_action):
    action, _contract = onboarding_action
    action._get_session_and_contract = AsyncMock(return_value=(None, None))

    result = json.loads(await action._handle_next_question())

    assert result["ok"] is False
    assert result["error_code"] == "NO_SESSION"
