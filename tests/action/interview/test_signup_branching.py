"""Signup interview branching — Saturday format and work-email employer paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.flow import (
    compute_collectible_path_names,
    resolve_next_field_name,
)
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture
def signup_action():
    action = InterviewAction()
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_weekday_slot_skips_training_format(signup_action):
    _, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")

    reachable = await compute_collectible_path_names(session, spec, lambda _n: None)
    assert "training_format" not in reachable
    assert "user_email" in reachable

    nxt = await resolve_next_field_name(session, spec, lambda _n: None)
    assert nxt == "user_email"


@pytest.mark.asyncio
async def test_saturday_slot_requires_training_format(signup_action):
    _, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")

    nxt = await resolve_next_field_name(session, spec, lambda _n: None)
    assert nxt == "training_format"


@pytest.mark.asyncio
async def test_work_email_branches_to_employer(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@mail.com")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    nxt = await resolve_next_field_name(session, spec, lambda _n: None)
    assert nxt == "employer_name"
    assert (
        "employer_name" not in session.fields
        or session.get_value("employer_name") is None
    )


@pytest.mark.asyncio
async def test_changing_slot_prunes_training_format(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
    session.set_value("training_format", "Virtual")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Monday 9:00 AM - 11:00 AM"}, visitor=None
        )
    )
    assert result["ok"] is True
    assert "training_format" not in session.fields
