"""Review-stage field updates — extract values from correction utterances."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.interview_loader import load_interview_spec
from jvagent.action.interview_action.session import InterviewSession, InterviewStatus

_SIGNUP_YAML = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview/interview.yaml"
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(_SIGNUP_YAML.parent.parent)})
    spec = load_interview_spec(str(_SIGNUP_YAML))
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
