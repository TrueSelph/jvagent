"""Batch set_fields / get_fields and correction paths."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import (
    CTX_QUESTION_PRESENTED,
    InterviewSession,
    InterviewStatus,
)
from jvagent.action.interview_action.interview_action import InterviewAction
from tests.action.interview_action.conftest import (
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
async def test_set_fields_batch_stores_multiple(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Jane Doe and jane@example.com")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["field"] == "user_name"
    assert session.get_value("user_name") == "Jane Doe"


@pytest.mark.asyncio
async def test_set_fields_correction_mid_active(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.context[CTX_QUESTION_PRESENTED] = "user_email"
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="change my email to eldon@mail.com")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "eldon@mail.com"


@pytest.mark.asyncio
async def test_get_fields_all_collected(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_fields(visitor=SimpleNamespace()))

    assert result["ok"] is True
    assert result["values"]["user_name"]["value"] == "Jane Doe"


@pytest.mark.asyncio
async def test_review_email_correction_via_set_fields(signup_action):
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
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "eldon@mail.com"
