"""Completion handler — interview__complete envelope."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import (
    InterviewSession,
    InterviewStatus,
)
from jvagent.action.interview_action.interview_action import InterviewAction

_SIGNUP_SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/actions/jvagent/interview_action/skills/signup_interview"
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
async def test_complete_returns_completion_result(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    session.skip_field("phone_number")

    conv = SimpleNamespace(
        context={"new_user": False, "signup_records": {"old": "data"}},
        save=AsyncMock(),
    )
    visitor = SimpleNamespace(conversation=conv, utterance="Looks good")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    action._close_task = AsyncMock()

    result = json.loads(await action._handle_complete(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert "completion_result" in result
    assert "thank you" in result["response_directive"].lower()
    assert "eldon" in result["response_directive"].lower()
    assert "interview" not in conv.context
    assert "signup_records" not in conv.context
    assert conv.context.get("new_user") is False
    conv.save.assert_awaited()
