"""Post-tool sidebars must still chain to optional next questions."""

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
from jvagent.action.interview_action.session import (
    CTX_QUESTION_PRESENTED,
    InterviewSession,
)

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
async def test_email_store_chains_to_optional_phone(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, _spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="user_email", value="jane@gmail.com")
    )

    assert result["ok"] is True
    assert result["next_tool"] == "interview__next_question"
    assert result["response_directive"] == "Call interview__next_question."


@pytest.mark.asyncio
async def test_work_email_post_tool_delivers_phone_followup(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, _spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="user_email", value="eldon@mail.com")
    )

    assert result["ok"] is True
    assert "next_tool" not in result
    directive = result["response_directive"]
    assert "work email" in directive.lower()
    assert "phone" in directive.lower()
    assert session.context.get(CTX_QUESTION_PRESENTED) == "phone_number"
