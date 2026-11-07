"""Completion handler — interview__complete envelope."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.engine import (
    REVIEW_PRESENTED_FIELDS_KEY,
    _fields_fingerprint,
)
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
async def test_complete_returns_completion_result(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup-interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    session.set_value("employer_name", "Example Employer")
    session.skip_field("phone_number")
    # This isolates the completion handler with an explicitly reviewed snapshot.
    session.context[REVIEW_PRESENTED_FIELDS_KEY] = _fields_fingerprint(
        session.get_collected_summary()
    )

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
    assert "recorded" in result["response_directive"].lower()
    assert "we will contact" not in result["response_directive"].lower()
    assert "no booking or notification" in result["response_directive"].lower()
    assert "interview" not in conv.context
    assert "signup_records" not in conv.context
    assert conv.context.get("new_user") is False
    conv.save.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_branch", [None, "training_format", "employer_name"])
async def test_incomplete_signup_does_not_report_success_or_close(
    signup_action, missing_branch
):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup-interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Jane Doe")
    if missing_branch:
        session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
        session.set_value("user_email", "jane@mail.com")
        session.set_value("training_format", "Virtual")
        session.set_value("employer_name", "Example Employer")
        session.fields.pop(missing_branch)
    session.context[REVIEW_PRESENTED_FIELDS_KEY] = _fields_fingerprint(
        session.get_collected_summary()
    )
    visitor = SimpleNamespace(conversation=SimpleNamespace(context={}), utterance="Yes")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    action._close_task = AsyncMock()

    result = json.loads(await action._handle_complete(visitor=visitor))

    assert result["ok"] is False
    assert result["status"] != "completed"
    assert session.status == InterviewStatus.REVIEW
    action._close_task.assert_not_awaited()
