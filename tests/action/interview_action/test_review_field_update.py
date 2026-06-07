"""Review-stage field updates — extract values from correction utterances."""

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
async def test_review_email_update_from_correction_utterance(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    session.skip_field("phone_number")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="change my email to eldon@mail.com")
    result = json.loads(
        await action._handle_set_field(
            field="user_email",
            value="eldon@mail.com",
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert result["stored"] is True
    assert session.get_value("user_email") == "eldon@mail.com"
    assert result["validated_from"] in ("utterance", "supplied_grounded")


@pytest.mark.asyncio
async def test_review_email_update_without_model_value_extracts_from_utterance(
    signup_action,
):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="change my email to eldon@mail.com")
    result = json.loads(
        await action._handle_set_field(
            field="user_email",
            value="",
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "eldon@mail.com"
