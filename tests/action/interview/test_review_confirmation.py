"""Review step — confirmation framing and auto-inline after skip."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import (
    InterviewSession,
    InterviewStatus,
)
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview.tools import build_tools
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_skip_field_tool_accepts_field_key(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    skip_tool = next(
        t for t in build_tools(action) if t.name == "interview__skip_field"
    )
    result = json.loads(
        (
            await skip_tool.call(field_key="phone_number", visitor=SimpleNamespace())
        ).content
    )

    assert result["ok"] is True
    assert "phone_number" in result.get("skipped_fields", [])


@pytest.mark.asyncio
async def test_skip_field_inlines_review_once(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_skip_field(field="phone_number", visitor=SimpleNamespace())
    )

    assert result["ok"] is True
    assert result.get("next_tool") == "interview__review"
    assert "interview__review" in (result.get("response_directive") or "")
    assert session.status == InterviewStatus.ACTIVE


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
    assert "phone" not in result["summary"].lower()
    assert "phone_number" not in result["fields"]


@pytest.mark.asyncio
async def test_review_omits_off_path_and_skipped_fields(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@gmail.com")
    session.skip_field("phone_number")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(await action._handle_review(visitor=SimpleNamespace()))

    summary = result["summary"].lower()
    assert "training_format" not in summary
    assert "employer_name" not in summary
    assert "phone" not in summary
    assert result["fields"] == {
        "user_name": "Jane Doe",
        "available_times": "Monday 9:00 AM - 11:00 AM",
        "user_email": "jane@gmail.com",
    }
