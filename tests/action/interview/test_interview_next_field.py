"""Tests for interview__next_field."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import (
    InterviewAction,
)
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
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
async def test_next_field_redirects_to_review_when_done(onboarding_action):
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

    result = json.loads(await action._handle_next_field())

    assert result["ok"] is True
    assert result["next_tool"] == "interview__review"
    assert "interview__review" in result["response_directive"]


@pytest.mark.asyncio
async def test_next_field_no_session_returns_ok_false(onboarding_action):
    action, _contract = onboarding_action
    action._get_session_and_contract = AsyncMock(return_value=(None, None))

    result = json.loads(await action._handle_next_field())

    assert result["ok"] is False
    assert result["error_code"] == "NO_SESSION"
    assert "use_skill" in result["response_directive"]
    assert "reply" in result["response_directive"].lower()


@pytest.mark.asyncio
async def test_next_field_return_is_slim(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    result = json.loads(await action._handle_next_field())

    nf = result["next_field"]
    assert "key" in nf and "prompt" in nf
    assert nf["required"] is True  # surfaced so the model can offer skip
    assert "guidance" not in nf  # guidance stays in field_reference only
    for gone in (
        "awaiting_fields",
        "field_keys",
        "guidance_page",
        "active_path_keys",
        "fields",
    ):
        assert gone not in result
    assert "response_directive" in result
