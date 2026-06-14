"""Review-stage field updates — extract values from correction utterances."""

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
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com"}, visitor=visitor
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["stored"] is True
    assert session.get_value("user_email") == "eldon@mail.com"


@pytest.mark.asyncio
async def test_review_slot_correction_accepts_validated_correction_without_utterance(
    signup_action,
):
    """Cross-turn ack: model may apply a validated slot correction without re-stating the slot."""
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    session.skip_field("phone_number")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Yes and virtually is fine")
    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Saturday 9:00 AM - 12:00 PM"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["stored"] is True
    assert session.get_value("available_times") == "Saturday 9:00 AM - 12:00 PM"
    assert result.get("next_tool") == "interview__next_field"


@pytest.mark.asyncio
async def test_review_pivot_stores_training_format_from_followup_utterance(
    signup_action,
):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.ACTIVE
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
    session.set_value("user_email", "eldon.marks@gmail.com")
    session.skip_field("phone_number")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Yes and virtually is fine")
    result = json.loads(
        await action._handle_set_fields(
            fields={"training_format": "Virtual"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("training_format") == "Virtual"
    assert result.get("next_tool") == "interview__review"
