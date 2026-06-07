"""Review step — confirmation framing and auto-inline after skip."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.session import InterviewSession, InterviewStatus

_SIGNUP_SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview"
)


@pytest.fixture
def signup_action():
    action = InterviewAction(
        metadata={"agent_dir": str(_SIGNUP_SKILL_DIR.parent.parent)}
    )
    spec = load_interview_spec_from_skill(_SIGNUP_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_skip_field_inlines_review_once(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_skip_field(field="phone_number", visitor=SimpleNamespace())
    )

    assert result["ok"] is True
    assert result["status"] == "review"
    assert result.get("review_ready") is True
    assert "next_tool" not in result
    directive = result["response_directive"].lower()
    assert "not complete" in directive or "not complete yet" in directive
    assert "interview__complete" in directive
    assert session.status == InterviewStatus.REVIEW


@pytest.mark.asyncio
async def test_review_directive_is_confirmation_not_completion(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    session.skip_field("phone_number")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(await action._handle_review(visitor=SimpleNamespace()))

    assert result["status"] == "review"
    directive = result["response_directive"].lower()
    assert "confirm" in directive
    assert "not complete" in directive or "not complete yet" in directive
    assert "jvagent training signup" in directive.lower() or "finalize" in directive
