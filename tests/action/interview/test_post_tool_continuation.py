"""Post-tool sidebars must still chain to optional next questions."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
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
async def test_email_store_chains_to_optional_phone(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, _spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(fields={"user_email": "jane@gmail.com"})
    )

    assert result["ok"] is True
    assert result.get("next_tool") == "interview__next_field"
    assert "Call interview__next_field" in (result.get("response_directive") or "")


@pytest.mark.asyncio
async def test_work_email_post_tool_delivers_employer_followup(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, _spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(fields={"user_email": "eldon@mail.com"})
    )

    assert result["ok"] is True
    directive = result.get("response_directive") or ""
    assert "work email" in directive.lower()
    assert "company" in directive.lower() or "organization" in directive.lower()
    assert "phone" not in directive.lower()
